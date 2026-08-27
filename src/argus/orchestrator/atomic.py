from __future__ import annotations

import asyncio
from datetime import datetime

from argus.contracts.models import CollectionStatus, SourceCoverage, StructuredError
from argus.crawler.errors import CrawlerRequestSkippedError
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

    execution_budget_version = "time-and-page-budget/1"

    def __init__(
        self,
        *args,
        source_task_timeout_seconds: float = 90.0,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.source_task_timeout_seconds = max(5.0, float(source_task_timeout_seconds))

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
        time_budget_seconds = float(record.request.constraints.max_duration_seconds)
        budget_started_at = await self._ensure_execution_budget_started(record)
        processed = len(visited)
        time_budget_exhausted = False

        while pending and processed < total_budget:
            if await self._is_cancelled(record.collection_id):
                return
            remaining_seconds = self._remaining_execution_seconds(
                budget_started_at,
                time_budget_seconds,
            )
            if remaining_seconds <= 0:
                time_budget_exhausted = True
                break

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
            task_timeout_seconds = min(
                self.source_task_timeout_seconds,
                remaining_seconds,
            )
            collection_deadline_bounds_task = (
                remaining_seconds <= self.source_task_timeout_seconds
            )

            try:
                async with asyncio.timeout(task_timeout_seconds):
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
            except CrawlerRequestSkippedError as exc:
                robots_blocked = exc.robots_txt
                await self._record_task_failure(
                    record,
                    task=task,
                    coverage=coverage,
                    error_code=(
                        "SOURCE_ROBOTS_TXT_BLOCKED"
                        if robots_blocked
                        else "SOURCE_REQUEST_SKIPPED"
                    ),
                    message=(
                        "Source URL is disallowed by robots.txt; ARGUS did not fetch or bypass it."
                        if robots_blocked
                        else f"Crawler skipped the source request before navigation ({exc.reason})."
                    ),
                    pending=pending,
                    visited=visited,
                    historical_branch_queries=historical_branch_queries,
                    processed=processed,
                    total_budget=total_budget,
                    retryable=not robots_blocked,
                    blocked=robots_blocked,
                )
                visited.add(key)
                processed += 1
                continue
            except TimeoutError:
                if collection_deadline_bounds_task:
                    pending.insert(0, task)
                    time_budget_exhausted = True
                    break
                await self._record_task_failure(
                    record,
                    task=task,
                    coverage=coverage,
                    error_code="SOURCE_TASK_TIMEOUT",
                    message=(
                        "Source task exceeded the bounded execution timeout "
                        f"of {self.source_task_timeout_seconds:g} seconds."
                    ),
                    pending=pending,
                    visited=visited,
                    historical_branch_queries=historical_branch_queries,
                    processed=processed,
                    total_budget=total_budget,
                )
                visited.add(key)
                processed += 1
                continue
            except Exception as exc:
                message = safe_error_message(exc, max_length=300)
                await self._record_task_failure(
                    record,
                    task=task,
                    coverage=coverage,
                    error_code="SOURCE_ERROR",
                    message=message,
                    pending=pending,
                    visited=visited,
                    historical_branch_queries=historical_branch_queries,
                    processed=processed,
                    total_budget=total_budget,
                )
                visited.add(key)
                processed += 1
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
        pending_work = bool(pending)
        page_budget_exhausted = pending_work and processed >= total_budget

        if time_budget_exhausted:
            self._append_budget_error(
                record,
                code="TIME_BUDGET_EXHAUSTED",
                message=(
                    "Collection stopped after reaching "
                    f"max_duration_seconds={time_budget_seconds:g}."
                ),
            )
            source_errors = True
        elif page_budget_exhausted:
            self._append_budget_error(
                record,
                code="PAGE_BUDGET_EXHAUSTED",
                message=f"Collection stopped after reaching max_pages={total_budget}.",
            )
            source_errors = True

        budget_exhausted = time_budget_exhausted or page_budget_exhausted
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
            if pending_work
            else [],
            "execution_budget": {
                "version": self.execution_budget_version,
                "started_at": budget_started_at.isoformat(),
                "max_pages": total_budget,
                "max_duration_seconds": time_budget_seconds,
                "source_task_timeout_seconds": self.source_task_timeout_seconds,
                "processed_pages": processed,
                "page_budget_exhausted": page_budget_exhausted,
                "time_budget_exhausted": time_budget_exhausted,
                "pending_tasks": len(pending),
            },
        }
        await self.repository.update_collection(record)
        logger.info(
            "collection finished",
            extra=_log_extra(record, "collection_finished"),
        )

    async def _record_task_failure(
        self,
        record,
        *,
        task: SourceTask,
        coverage: SourceCoverage,
        error_code: str,
        message: str,
        pending: list[SourceTask],
        visited: set[str],
        historical_branch_queries: set[str],
        processed: int,
        total_budget: int,
        retryable: bool = True,
        blocked: bool = False,
    ) -> None:
        coverage.status = "blocked" if blocked else "error"
        coverage.blocked = blocked
        coverage.error_code = error_code
        coverage.error_message = message
        record.errors.append(
            StructuredError(
                code=error_code,
                message=message,
                retryable=retryable,
                source_id=task.source_id,
            )
        )
        logger.warning(
            "source blocked" if blocked else "source collection failed",
            extra=_log_extra(
                record,
                "source_blocked" if blocked else "source_error",
                source_id=task.source_id,
                error_code=error_code,
            ),
        )
        coverage.finished_at = now()
        record.coverage.append(coverage)
        next_visited = {*visited, task.dedupe_key}
        next_processed = processed + 1
        record.progress_percent = min(
            99,
            int(next_processed / max(1, total_budget) * 100),
        )
        record.stage = f"collecting:{task.source_id}"
        record.updated_at = now()
        record.checkpoint = {
            **record.checkpoint,
            "visited": sorted(next_visited),
            "historical_branch_queries": sorted(historical_branch_queries),
            "pending_tasks": [self._task_dict(item) for item in pending],
        }
        await self.repository.update_collection(record)

    async def _ensure_execution_budget_started(self, record) -> datetime:
        raw = record.checkpoint.get("execution_budget_started_at")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                parsed = None
            if parsed is not None and parsed.tzinfo is not None:
                return parsed

        started_at = now()
        record.checkpoint = {
            **record.checkpoint,
            "execution_budget_started_at": started_at.isoformat(),
        }
        record.updated_at = now()
        await self.repository.update_collection(record)
        return started_at

    @staticmethod
    def _remaining_execution_seconds(started_at: datetime, max_duration_seconds: float) -> float:
        elapsed = max(0.0, (now() - started_at).total_seconds())
        return max(0.0, max_duration_seconds - elapsed)

    @staticmethod
    def _append_budget_error(record, *, code: str, message: str) -> None:
        if any(error.code == code for error in record.errors):
            return
        record.errors.append(
            StructuredError(
                code=code,
                message=message,
                retryable=False,
                source_id="research_budget",
            )
        )
