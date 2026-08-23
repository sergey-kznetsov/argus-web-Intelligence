from __future__ import annotations

import asyncio

from argus.contracts.models import CollectionStatus, SourceCoverage, StructuredError
from argus.history.snapshots import stage_snapshots
from argus.orchestrator.service import (
    CollectionOrchestrator,
    _blocking_error,
    _log_extra,
    logger,
    now,
)
from argus.security.redaction import safe_error_message
from argus.sources.base import SourceTask


class AtomicCollectionOrchestrator(CollectionOrchestrator):
    """Collection orchestrator with atomic factual persistence per successful source task."""

    async def _commit_task_success(
        self,
        record,
        *,
        observations,
        evidence,
        snapshots,
    ) -> None:
        commit = getattr(self.repository, "commit_task_success", None)
        if not callable(commit):
            raise RuntimeError("repository does not support atomic task commits")
        await commit(
            record,
            observations=observations,
            evidence=evidence,
            snapshots=snapshots,
        )

    async def _process_tasks(self, record, pending: list[SourceTask]) -> None:
        visited = set(record.checkpoint.get("visited", []))
        historical_branch_queries = set(
            record.checkpoint.get("historical_branch_queries", [])
        )
        total_budget = record.request.constraints.max_pages
        processed = len(visited)

        while pending and processed < total_budget:
            if await self._is_cancelled(record.collection_id):
                return
            task = pending.pop(0)
            key = task.dedupe_key
            if key in visited:
                continue
            adapter = self.registry.get(task.source_id)
            coverage = SourceCoverage(
                source_id=task.source_id,
                status="running",
                started_at=now(),
            )

            try:
                with stage_snapshots() as snapshot_batch:
                    fetched = await adapter.fetch(task)
                    result = await adapter.normalize(
                        await adapter.extract(task, fetched, record.request)
                    )

                    working_record = record.model_copy(deep=True)
                    working_pending = list(pending)
                    working_visited = set(visited)
                    working_historical_queries = set(historical_branch_queries)

                    working_record.errors.extend(result.errors)
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
                                working_record,
                                "source_blocked",
                                source_id=task.source_id,
                                error_code=coverage.error_code,
                            ),
                        )
                    elif result.partial or result.errors:
                        logger.warning(
                            "source result degraded",
                            extra=_log_extra(
                                working_record,
                                "source_degraded",
                                source_id=task.source_id,
                                error_code=coverage.error_code,
                            ),
                        )

                    queued_keys = {item.dedupe_key for item in working_pending}
                    for child in result.discovered_tasks:
                        child.metadata["collection_id"] = working_record.collection_id
                        child_key = child.dedupe_key
                        if child_key not in working_visited and child_key not in queued_keys:
                            queued_keys.add(child_key)
                            working_pending.append(child)

                    try:
                        await self._expand_historical(
                            working_record,
                            task,
                            result.observations,
                            working_pending,
                            working_visited,
                            working_historical_queries,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        message = safe_error_message(exc, max_length=300)
                        working_record.errors.append(
                            StructuredError(
                                code="HISTORICAL_BRANCH_ERROR",
                                message=message,
                                retryable=True,
                                source_id=task.source_id,
                            )
                        )
                        if coverage.status == "ok":
                            coverage.status = "partial"
                        if coverage.error_code is None:
                            coverage.error_code = "HISTORICAL_BRANCH_ERROR"
                            coverage.error_message = message
                        logger.warning(
                            "historical branch failed after factual extraction",
                            extra=_log_extra(
                                working_record,
                                "historical_branch_error",
                                source_id=task.source_id,
                                error_code="HISTORICAL_BRANCH_ERROR",
                            ),
                        )

                    coverage.finished_at = now()
                    working_record.coverage.append(coverage)
                    working_visited.add(key)
                    working_processed = processed + 1
                    working_record.progress_percent = min(
                        99,
                        int(working_processed / max(1, total_budget) * 100),
                    )
                    working_record.stage = f"collecting:{task.source_id}"
                    working_record.updated_at = now()
                    working_record.checkpoint = {
                        **working_record.checkpoint,
                        "visited": sorted(working_visited),
                        "historical_branch_queries": sorted(working_historical_queries),
                        "pending_tasks": [
                            self._task_dict(item) for item in working_pending
                        ],
                    }
                    task_snapshots = list(snapshot_batch.snapshots)

            except asyncio.CancelledError:
                raise
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
                record.progress_percent = min(
                    99,
                    int(processed / max(1, total_budget) * 100),
                )
                record.stage = f"collecting:{task.source_id}"
                record.updated_at = now()
                record.checkpoint = {
                    **record.checkpoint,
                    "visited": sorted(visited),
                    "historical_branch_queries": sorted(historical_branch_queries),
                    "pending_tasks": [self._task_dict(item) for item in pending],
                }
                await self.repository.update_collection(record)
                continue

            # Storage commit is deliberately outside the source-error handler. Any database
            # failure or lost lease aborts this worker attempt with the task still unvisited
            # in persisted state, so a replacement worker can replay it safely.
            await self._commit_task_success(
                working_record,
                observations=result.observations,
                evidence=result.evidence,
                snapshots=task_snapshots,
            )

            record = working_record
            pending = working_pending
            visited = working_visited
            historical_branch_queries = working_historical_queries
            processed = working_processed

        if await self._is_cancelled(record.collection_id):
            return
        observations = await self.repository.list_observations(record.collection_id)
        blocked = any(item.blocked for item in record.coverage)
        source_partial = any(item.status == "partial" for item in record.coverage)
        non_blocking_errors = [
            error for error in record.errors if not _blocking_error(error)
        ]
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

        if observations and (
            blocked or source_partial or source_errors or budget_exhausted
        ):
            if record.request.allow_partial:
                record.status = CollectionStatus.PARTIAL
                record.partial = True
            else:
                record.status = CollectionStatus.FAILED
                record.partial = False
        elif not observations and blocked and not source_errors:
            record.status = CollectionStatus.BLOCKED
            record.partial = False
        elif not observations and (
            source_partial or source_errors or budget_exhausted
        ):
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
            "pending_tasks": [self._task_dict(item) for item in pending]
            if budget_exhausted
            else [],
        }
        await self.repository.update_collection(record)
        logger.info(
            "collection finished",
            extra=_log_extra(record, "collection_finished"),
        )
