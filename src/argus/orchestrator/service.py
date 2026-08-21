from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

from argus.contracts.models import (
    CollectionAccepted,
    CollectionRecord,
    CollectionRequest,
    CollectionResult,
    CollectionStatus,
    SourceCoverage,
    StructuredError,
)
from argus.research.discovery import DiscoveryService
from argus.research.planner import ResearchPlanner
from argus.security.redaction import safe_error_message
from argus.sources.base import SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.base import Repository

logger = logging.getLogger("argus.orchestrator")


def now():
    return datetime.now(UTC)


def _log_extra(record: CollectionRecord, event: str, **values: object) -> dict[str, object]:
    return {
        "event": event,
        "collection_id": record.collection_id,
        "analysis_id": record.request.analysis_id,
        "consumer": record.request.consumer,
        "stage": record.stage,
        "status": record.status.value,
        **values,
    }


class CollectionOrchestrator:
    def __init__(
        self,
        repository: Repository,
        registry: SourceRegistry,
        planner: ResearchPlanner,
        max_concurrency: int = 4,
        discovery: DiscoveryService | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.planner = planner
        self.discovery = discovery
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()

    async def start(self) -> None:
        await self.repository.initialize()
        for record in await self.repository.list_recoverable_collections():
            if record.status == CollectionStatus.RUNNING:
                record.status = CollectionStatus.QUEUED
                record.stage = "recovered"
                record.updated_at = now()
                await self.repository.update_collection(record)
            logger.info("recovering collection", extra=_log_extra(record, "collection_recovered"))
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
        logger.info("collection accepted", extra=_log_extra(record, "collection_accepted"))
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
        if record.status in {
            CollectionStatus.COMPLETED,
            CollectionStatus.PARTIAL,
            CollectionStatus.BLOCKED,
            CollectionStatus.FAILED,
            CollectionStatus.CANCELLED,
        }:
            return record
        self._cancelled.add(collection_id)
        task = self._jobs.get(collection_id)
        if task:
            task.cancel()
        record.status = CollectionStatus.CANCELLED
        record.stage = "cancelled"
        record.updated_at = now()
        await self.repository.update_collection(record)
        logger.info("collection cancelled", extra=_log_extra(record, "collection_cancelled"))
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
            logger.info("collection started", extra=_log_extra(record, "collection_started"))
            try:
                plan = await self.planner.plan(record.request)
                pending = self._load_tasks(record)
                discovery_providers: list[str] = list(
                    record.checkpoint.get("discovery_providers", [])
                )
                if not pending:
                    pending = await self._initial_tasks(record)
                    if self.discovery is not None and plan.queries:
                        record.stage = "discovery"
                        record.updated_at = now()
                        await self.repository.update_collection(record)
                        outcome = await self.discovery.discover(plan.queries, record.request)
                        discovery_providers = outcome.providers_attempted
                        record.errors.extend(outcome.errors)
                        pending = self._merge_tasks(pending, outcome.tasks, record.collection_id)
                        if outcome.errors:
                            logger.warning(
                                "discovery provider degraded",
                                extra=_log_extra(
                                    record,
                                    "discovery_error",
                                    error_code="DISCOVERY_ERROR",
                                ),
                            )
                    pending = self._merge_tasks(pending, plan.tasks, record.collection_id)
                record.checkpoint = {
                    "queries": plan.queries,
                    "planner_notes": plan.notes,
                    "discovery_providers": discovery_providers,
                    "pending_tasks": [self._task_dict(t) for t in pending],
                    "visited": record.checkpoint.get("visited", []),
                }
                await self.repository.update_collection(record)
                if not pending:
                    record.errors.append(
                        StructuredError(
                            code="NO_SOURCE_TASKS",
                            message=(
                                "Research plan produced no executable source tasks. "
                                "Provide seed URLs or configure a discovery provider."
                            ),
                            retryable=False,
                        )
                    )
                    record.status = CollectionStatus.FAILED
                    record.stage = "failed:no_sources"
                    record.updated_at = now()
                    await self.repository.update_collection(record)
                    logger.warning(
                        "collection has no executable sources",
                        extra=_log_extra(
                            record,
                            "collection_failed",
                            error_code="NO_SOURCE_TASKS",
                        ),
                    )
                    return
                await self._process_tasks(record, pending)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                record = await self.repository.get_collection(collection_id) or record
                message = safe_error_message(exc, max_length=500)
                record.errors.append(
                    StructuredError(
                        code="COLLECTION_FAILED",
                        message=message,
                        retryable=False,
                    )
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
                logger.error(
                    "collection failed",
                    extra=_log_extra(
                        record,
                        "collection_failed",
                        error_code="COLLECTION_FAILED",
                    ),
                )
            finally:
                self._cancelled.discard(collection_id)

    async def _initial_tasks(self, record: CollectionRecord) -> list[SourceTask]:
        tasks: list[SourceTask] = []
        seen: set[str] = set()
        for adapter in self.registry.for_intents(record.request.intents):
            for task in await adapter.discover(record.request):
                task.metadata["collection_id"] = record.collection_id
                key = f"{task.source_id}:{task.url}"
                if key not in seen:
                    seen.add(key)
                    tasks.append(task)
        return tasks

    @staticmethod
    def _merge_tasks(
        existing: list[SourceTask],
        additions: list[SourceTask],
        collection_id: str,
    ) -> list[SourceTask]:
        keys = {f"{task.source_id}:{task.url}" for task in existing}
        for task in additions:
            task.metadata["collection_id"] = collection_id
            key = f"{task.source_id}:{task.url}"
            if key not in keys:
                keys.add(key)
                existing.append(task)
        return existing

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
                if result.blocked:
                    logger.warning(
                        "source blocked",
                        extra=_log_extra(
                            record,
                            "source_blocked",
                            source_id=task.source_id,
                        ),
                    )
                queued_keys = {f"{item.source_id}:{item.url}" for item in pending}
                for child in result.discovered_tasks:
                    child.metadata["collection_id"] = record.collection_id
                    child_key = f"{child.source_id}:{child.url}"
                    if child_key not in visited and child_key not in queued_keys:
                        queued_keys.add(child_key)
                        pending.append(child)
            except Exception as exc:
                message = safe_error_message(exc, max_length=300)
                coverage.status = "error"
                coverage.error_code = "SOURCE_ERROR"
                coverage.error_message = message
                record.errors.append(
                    StructuredError(
                        code="SOURCE_ERROR",
                        message=message,
                        retryable=True,
                        source_id=task.source_id,
                    )
                )
                logger.warning(
                    "source collection failed",
                    extra=_log_extra(
                        record,
                        "source_error",
                        source_id=task.source_id,
                        error_code="SOURCE_ERROR",
                    ),
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
        blocked = any(item.blocked for item in record.coverage)
        source_errors = bool(record.errors)
        budget_exhausted = bool(pending)
        if budget_exhausted:
            record.errors.append(
                StructuredError(
                    code="PAGE_BUDGET_EXHAUSTED",
                    message=f"Collection stopped after reaching max_pages={total_budget}.",
                    retryable=False,
                )
            )

        if observations and (blocked or source_errors or budget_exhausted):
            if record.request.allow_partial:
                record.status = CollectionStatus.PARTIAL
                record.partial = True
            else:
                record.status = CollectionStatus.FAILED
                record.partial = False
        elif not observations and blocked and not source_errors:
            record.status = CollectionStatus.BLOCKED
            record.partial = False
        elif not observations and (source_errors or budget_exhausted):
            record.status = CollectionStatus.FAILED
            record.partial = False
        else:
            record.status = CollectionStatus.COMPLETED
            record.partial = False

        record.progress_percent = 100
        record.stage = record.status.value
        record.updated_at = now()
        record.checkpoint = {
            **record.checkpoint,
            "pending_tasks": [self._task_dict(t) for t in pending] if budget_exhausted else [],
        }
        await self.repository.update_collection(record)
        logger.info("collection finished", extra=_log_extra(record, "collection_finished"))

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
