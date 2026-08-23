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
    Observation,
    SourceCoverage,
    StructuredError,
)
from argus.research.discovery import DiscoveryService
from argus.research.historical import HistoricalBranchPlanner
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


def _blocking_error(error: StructuredError) -> bool:
    return error.code.endswith("_BLOCKED") or error.code == "DISCOVERY_BLOCKED"


class CollectionOrchestrator:
    def __init__(
        self,
        repository: Repository,
        registry: SourceRegistry,
        planner: ResearchPlanner,
        max_concurrency: int = 4,
        discovery: DiscoveryService | None = None,
        historical_branch_planner: HistoricalBranchPlanner | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.planner = planner
        self.discovery = discovery
        self.historical_branch_planner = historical_branch_planner
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
                planning_complete = bool(record.checkpoint.get("planning_complete", False))
                covered_intents = set(record.checkpoint.get("covered_intents", []))
                discovery_providers: list[str] = list(
                    record.checkpoint.get("discovery_providers", [])
                )
                discovery_queries: list[str] = list(
                    record.checkpoint.get("discovery_queries", [])
                )
                discovery_blocked = bool(record.checkpoint.get("discovery_blocked", False))

                if not planning_complete:
                    pending, covered_intents = await self._initial_tasks(record)
                    uncovered_intents = [
                        intent for intent in record.request.intents if intent not in covered_intents
                    ]
                    if self.discovery is not None and uncovered_intents:
                        (
                            pending,
                            discovery_queries,
                            discovery_providers,
                            discovery_blocked,
                        ) = await self._discover_uncovered_intents(
                            record,
                            pending,
                            uncovered_intents,
                        )
                    pending = self._merge_tasks(pending, plan.tasks, record.collection_id)
                    planning_complete = True

                record.checkpoint = {
                    "queries": plan.queries,
                    "planner_notes": plan.notes,
                    "planning_complete": planning_complete,
                    "covered_intents": sorted(covered_intents),
                    "discovery_queries": discovery_queries,
                    "discovery_providers": discovery_providers,
                    "discovery_blocked": discovery_blocked,
                    "historical_branch_queries": record.checkpoint.get(
                        "historical_branch_queries", []
                    ),
                    "pending_tasks": [self._task_dict(t) for t in pending],
                    "visited": record.checkpoint.get("visited", []),
                }
                await self.repository.update_collection(record)
                if not pending and not record.checkpoint.get("visited"):
                    if discovery_blocked:
                        record.status = CollectionStatus.BLOCKED
                        record.partial = False
                        record.progress_percent = 100
                        record.stage = "blocked:discovery"
                        record.updated_at = now()
                        await self.repository.update_collection(record)
                        logger.warning(
                            "collection discovery blocked",
                            extra=_log_extra(
                                record,
                                "collection_blocked",
                                error_code="DISCOVERY_BLOCKED",
                            ),
                        )
                        return
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

    async def _discover_uncovered_intents(
        self,
        record: CollectionRecord,
        pending: list[SourceTask],
        uncovered_intents: list[str],
    ) -> tuple[list[SourceTask], list[str], list[str], bool]:
        if self.discovery is None:
            return pending, [], [], False

        query_budget = max(1, int(getattr(self.discovery, "max_queries", 8)))
        remaining_queries = query_budget
        discovery_queries: list[str] = []
        discovery_providers: list[str] = []
        discovery_blocked = False

        record.stage = "discovery"
        record.updated_at = now()
        await self.repository.update_collection(record)

        for index, intent in enumerate(uncovered_intents):
            if remaining_queries <= 0:
                remaining_intents = uncovered_intents[index:]
                record.errors.append(
                    StructuredError(
                        code="DISCOVERY_QUERY_BUDGET_EXHAUSTED",
                        message=(
                            "Discovery query budget was exhausted before all uncovered intents "
                            f"could be researched: {', '.join(remaining_intents)}"
                        ),
                        retryable=False,
                        source_id="discovery",
                    )
                )
                break

            intent_request = record.request.model_copy(update={"intents": [intent]})
            intent_plan = await self.planner.plan(intent_request)
            remaining_intent_count = len(uncovered_intents) - index
            allocation = max(1, remaining_queries // remaining_intent_count)
            queries = [query for query in intent_plan.queries if query.strip()][:allocation]
            if not queries:
                record.errors.append(
                    StructuredError(
                        code="DISCOVERY_NO_QUERIES",
                        message=f"Research planner produced no discovery queries for intent '{intent}'.",
                        retryable=False,
                        source_id="discovery",
                    )
                )
                continue

            remaining_queries -= len(queries)
            discovery_queries.extend(queries)
            outcome = await self.discovery.discover(queries, intent_request)
            discovery_blocked = discovery_blocked or outcome.blocked
            for provider in outcome.providers_attempted:
                if provider not in discovery_providers:
                    discovery_providers.append(provider)
            record.errors.extend(outcome.errors)
            pending = self._merge_tasks(pending, outcome.tasks, record.collection_id)

            if outcome.errors:
                logger.warning(
                    "discovery provider degraded",
                    extra=_log_extra(
                        record,
                        "discovery_error",
                        intent=intent,
                        error_code=(
                            "DISCOVERY_BLOCKED" if outcome.blocked else outcome.errors[0].code
                        ),
                    ),
                )

        return pending, discovery_queries, discovery_providers, discovery_blocked

    async def _initial_tasks(self, record: CollectionRecord) -> tuple[list[SourceTask], set[str]]:
        tasks: list[SourceTask] = []
        seen: set[str] = set()
        covered_intents: set[str] = set()
        requested_intents = set(record.request.intents)
        for adapter in self.registry.for_intents(record.request.intents):
            discovered = await adapter.discover(record.request)
            if discovered and "*" not in adapter.intents:
                covered_intents.update(requested_intents & adapter.intents)
            for task in discovered:
                task.metadata["collection_id"] = record.collection_id
                key = task.dedupe_key
                if key not in seen:
                    seen.add(key)
                    tasks.append(task)
        return tasks, covered_intents

    @staticmethod
    def _merge_tasks(
        existing: list[SourceTask],
        additions: list[SourceTask],
        collection_id: str,
    ) -> list[SourceTask]:
        keys = {task.dedupe_key for task in existing}
        for task in additions:
            task.metadata["collection_id"] = collection_id
            key = task.dedupe_key
            if key not in keys:
                keys.add(key)
                existing.append(task)
        return existing

    async def _process_tasks(self, record: CollectionRecord, pending: list[SourceTask]) -> None:
        visited = set(record.checkpoint.get("visited", []))
        historical_branch_queries = set(
            record.checkpoint.get("historical_branch_queries", [])
        )
        total_budget = record.request.constraints.max_pages
        processed = len(visited)
        while pending and processed < total_budget:
            if record.collection_id in self._cancelled:
                return
            task = pending.pop(0)
            key = task.dedupe_key
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

                record.errors.extend(result.errors)
                coverage.observations = len(result.observations)
                coverage.blocked = result.blocked
                if result.errors:
                    coverage.error_code = result.errors[0].code
                    coverage.error_message = result.errors[0].message
                if result.blocked:
                    coverage.status = "blocked"
                elif result.partial:
                    coverage.status = "partial"
                elif result.errors and not result.observations:
                    coverage.status = "error"
                else:
                    coverage.status = "ok"

                if result.blocked:
                    logger.warning(
                        "source blocked",
                        extra=_log_extra(
                            record,
                            "source_blocked",
                            source_id=task.source_id,
                            error_code=coverage.error_code,
                        ),
                    )
                elif result.partial or result.errors:
                    logger.warning(
                        "source result degraded",
                        extra=_log_extra(
                            record,
                            "source_degraded",
                            source_id=task.source_id,
                            error_code=coverage.error_code,
                        ),
                    )

                queued_keys = {item.dedupe_key for item in pending}
                for child in result.discovered_tasks:
                    child.metadata["collection_id"] = record.collection_id
                    child_key = child.dedupe_key
                    if child_key not in visited and child_key not in queued_keys:
                        queued_keys.add(child_key)
                        pending.append(child)

                await self._expand_historical(
                    record,
                    task,
                    result.observations,
                    pending,
                    visited,
                    historical_branch_queries,
                )
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
                "historical_branch_queries": sorted(historical_branch_queries),
                "pending_tasks": [self._task_dict(t) for t in pending],
            }
            await self.repository.update_collection(record)

        observations = await self.repository.list_observations(record.collection_id)
        blocked = any(item.blocked for item in record.coverage)
        source_partial = any(item.status == "partial" for item in record.coverage)
        non_blocking_errors = [error for error in record.errors if not _blocking_error(error)]
        source_errors = bool(non_blocking_errors)
        budget_exhausted = bool(pending)
        if budget_exhausted:
            record.errors.append(
                StructuredError(
                    code="PAGE_BUDGET_EXHAUSTED",
                    message=f"Collection stopped after reaching max_pages={total_budget}.",
                    retryable=False,
                )
            )
            source_errors = True

        if observations and (blocked or source_partial or source_errors or budget_exhausted):
            if record.request.allow_partial:
                record.status = CollectionStatus.PARTIAL
                record.partial = True
            else:
                record.status = CollectionStatus.FAILED
                record.partial = False
        elif not observations and blocked and not source_errors:
            record.status = CollectionStatus.BLOCKED
            record.partial = False
        elif not observations and (source_partial or source_errors or budget_exhausted):
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
            "historical_branch_queries": sorted(historical_branch_queries),
            "pending_tasks": [self._task_dict(t) for t in pending] if budget_exhausted else [],
        }
        await self.repository.update_collection(record)
        logger.info("collection finished", extra=_log_extra(record, "collection_finished"))

    async def _expand_historical(
        self,
        record: CollectionRecord,
        task: SourceTask,
        observations: list[Observation],
        pending: list[SourceTask],
        visited: set[str],
        seen_queries: set[str],
    ) -> None:
        if (
            self.discovery is None
            or self.historical_branch_planner is None
            or "historical_context" not in record.request.intents
            or not observations
        ):
            return

        raw_depth = task.metadata.get("historical_branch_depth", 0)
        try:
            branch_depth = max(0, int(raw_depth))
        except (TypeError, ValueError):
            branch_depth = 0
        if branch_depth >= record.request.constraints.max_depth:
            return

        queries = self.historical_branch_planner.expand(
            record.request,
            observations,
            seen_queries=seen_queries,
        )
        if not queries:
            return
        seen_queries.update(queries)

        branch_request = record.request.model_copy(update={"intents": ["historical_context"]})
        outcome = await self.discovery.discover(queries, branch_request)
        for error in outcome.errors:
            if error.code != "DISCOVERY_NO_RESULTS":
                record.errors.append(error)

        additions: list[SourceTask] = []
        for branch_task in outcome.tasks:
            if branch_task.dedupe_key in visited:
                continue
            branch_task.metadata["historical_branch_depth"] = branch_depth + 1
            branch_task.metadata["historical_branch_from"] = task.url
            branch_task.metadata["historical_branch_queries"] = list(queries)
            additions.append(branch_task)
        self._merge_tasks(pending, additions, record.collection_id)

        logger.info(
            "historical branch expanded",
            extra=_log_extra(
                record,
                "historical_branch",
                source_id=task.source_id,
                queries=len(queries),
                discovered_tasks=len(additions),
                branch_depth=branch_depth + 1,
            ),
        )

    @staticmethod
    def _task_dict(task: SourceTask) -> dict[str, object]:
        return {
            "source_id": task.source_id,
            "goal": task.goal,
            "url": task.url,
            "depth": task.depth,
            "metadata": task.metadata,
            "task_key": task.task_key,
        }

    @staticmethod
    def _load_tasks(record: CollectionRecord) -> list[SourceTask]:
        return [SourceTask(**item) for item in record.checkpoint.get("pending_tasks", [])]
