from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from argus.contracts.models import (
    CollectionAccepted, CollectionRecord, CollectionRequest, CollectionResult,
    CollectionStatus, SourceCoverage, StructuredError,
)
from argus.research.planner import ResearchPlanner
from argus.sources.base import SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.base import Repository


def now():
    return datetime.now(UTC)


class CollectionOrchestrator:
    def __init__(self, repository: Repository, registry: SourceRegistry, planner: ResearchPlanner,
                 max_concurrency: int = 4) -> None:
        self.repository = repository
        self.registry = registry
        self.planner = planner
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()

    async def start(self) -> None:
        await self.repository.initialize()
        for record in await self.repository.list_recoverable_collections():
            if record.status == CollectionStatus.RUNNING:
                record.status = CollectionStatus.QUEUED
                record.updated_at = now()
                await self.repository.update_collection(record)
            self._spawn(record.collection_id)

    async def shutdown(self) -> None:
        tasks = [task for task in self._jobs.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def submit(self, request: CollectionRequest) -> CollectionAccepted:
        collection_id = str(uuid4())
        ts = now()
        record = CollectionRecord(
            collection_id=collection_id,
            request=request,
            status=CollectionStatus.QUEUED,
            created_at=ts,
            updated_at=ts,
            stage="queued",
        )
        await self.repository.create_collection(record)
        self._spawn(collection_id)
        return CollectionAccepted(collection_id=collection_id)

    def _spawn(self, collection_id: str) -> None:
        existing = self._jobs.get(collection_id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(self._run(collection_id), name=f"argus:{collection_id}")
        self._jobs[collection_id] = task
        task.add_done_callback(lambda _: self._jobs.pop(collection_id, None))

    async def cancel(self, collection_id: str) -> CollectionRecord | None:
        record = await self.repository.get_collection(collection_id)
        if not record:
            return None
        self._cancelled.add(collection_id)
        task = self._jobs.get(collection_id)
        if task:
            task.cancel()
        record.status = CollectionStatus.CANCELLED
        record.stage = "cancelled"
        record.updated_at = now()
        await self.repository.update_collection(record)
        return record

    async def result(self, collection_id: str) -> CollectionResult | None:
        record = await self.repository.get_collection(collection_id)
        if not record:
            return None
        observations = await self.repository.list_observations(collection_id)
        evidence = await self.repository.list_evidence(collection_id)
        return CollectionResult(
            collection_id=collection_id,
            analysis_id=record.request.analysis_id,
            consumer=record.request.consumer,
            status=record.status,
            partial=record.partial,
            observations=observations,
            evidence=evidence,
            coverage=record.coverage,
            errors=record.errors,
        )

    async def _run(self, collection_id: str) -> None:
        async with self._semaphore:
            record = await self.repository.get_collection(collection_id)
            if not record or record.status == CollectionStatus.CANCELLED:
                return
            record.status = CollectionStatus.RUNNING
            record.stage = "research_planning"
            record.updated_at = now()
            await self.repository.update_collection(record)
            try:
                plan = await self.planner.plan(record.request)
                pending = self._load_tasks(record)
                if not pending:
                    pending = await self._initial_tasks(record)
                record.checkpoint = {
                    "queries": plan.queries,
                    "pending_tasks": [self._task_dict(t) for t in pending],
                    "visited": record.checkpoint.get("visited", []),
                }
                await self.repository.update_collection(record)
                await self._process_tasks(record, pending)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                record = await self.repository.get_collection(collection_id) or record
                record.errors.append(
                    StructuredError(code="COLLECTION_FAILED", message=str(exc)[:500], retryable=False)
                )
                has_data = bool(await self.repository.list_observations(collection_id))
                record.status = (
                    CollectionStatus.PARTIAL
                    if record.request.allow_partial and has_data
                    else CollectionStatus.FAILED
                )
                record.partial = record.status == CollectionStatus.PARTIAL
                record.stage = "failed"
                record.updated_at = now()
                await self.repository.update_collection(record)

    async def _initial_tasks(self, record: CollectionRecord) -> list[SourceTask]:
        tasks: list[SourceTask] = []
        for adapter in self.registry.for_intents(record.request.intents):
            for task in await adapter.discover(record.request):
                task.metadata["collection_id"] = record.collection_id
                tasks.append(task)
        return tasks

    async def _process_tasks(self, record: CollectionRecord, pending: list[SourceTask]) -> None:
        visited = set(record.checkpoint.get("visited", []))
        total_budget = record.request.constraints.max_pages
        processed = len(visited)
        while pending and processed < total_budget:
            if record.collection_id in self._cancelled:
                return
            task = pending.pop(0)
            key = f"{task.source_id}:{task.url}"
            if key in visited:
                continue
            adapter = self.registry.get(task.source_id)
            coverage = SourceCoverage(source_id=task.source_id, status="running", started_at=now())
            try:
                fetched = await adapter.fetch(task)
                result = await adapter.normalize(await adapter.extract(task, fetched, record.request))
                for observation in result.observations:
                    await self.repository.add_observation(observation)
                for evidence in result.evidence:
                    await self.repository.add_evidence(evidence, record.collection_id)
                coverage.observations = len(result.observations)
                coverage.blocked = result.blocked
                coverage.status = "blocked" if result.blocked else "ok"
                for child in result.discovered_tasks:
                    child.metadata["collection_id"] = record.collection_id
                    child_key = f"{child.source_id}:{child.url}"
                    if child_key not in visited:
                        pending.append(child)
            except Exception as exc:
                coverage.status = "error"
                coverage.error_code = "SOURCE_ERROR"
                coverage.error_message = str(exc)[:300]
                record.errors.append(
                    StructuredError(
                        code="SOURCE_ERROR",
                        message=str(exc)[:300],
                        retryable=True,
                        source_id=task.source_id,
                    )
                )
            coverage.finished_at = now()
            record.coverage.append(coverage)
            visited.add(key)
            processed += 1
            record.progress_percent = min(99, int(processed / max(1, total_budget) * 100))
            record.stage = f"collecting:{task.source_id}"
            record.updated_at = now()
            record.checkpoint = {
                **record.checkpoint,
                "visited": sorted(visited),
                "pending_tasks": [self._task_dict(t) for t in pending],
            }
            await self.repository.update_collection(record)

        observations = await self.repository.list_observations(record.collection_id)
        had_errors = bool(record.errors) or any(item.blocked for item in record.coverage)
        if had_errors and record.request.allow_partial and observations:
            record.status = CollectionStatus.PARTIAL
            record.partial = True
        elif had_errors and not observations:
            record.status = CollectionStatus.FAILED
        else:
            record.status = CollectionStatus.COMPLETED
        record.progress_percent = 100
        record.stage = "completed"
        record.updated_at = now()
        record.checkpoint = {**record.checkpoint, "pending_tasks": []}
        await self.repository.update_collection(record)

    @staticmethod
    def _task_dict(task: SourceTask) -> dict[str, object]:
        return {
            "source_id": task.source_id,
            "goal": task.goal,
            "url": task.url,
            "depth": task.depth,
            "metadata": task.metadata,
        }

    @staticmethod
    def _load_tasks(record: CollectionRecord) -> list[SourceTask]:
        return [SourceTask(**item) for item in record.checkpoint.get("pending_tasks", [])]
