from __future__ import annotations

import asyncio

from argus.consumer_delivery import ConsumerDeliveryProjector
from argus.contracts.models import SourceCoverage
from argus.crawler.errors import CrawlerRequestSkippedError
from argus.history.snapshots import stage_snapshots
from argus.normalization.public_map_provenance import classify_public_map_url
from argus.orchestrator.evidence_status import EvidenceStatusAdaptiveResearchOrchestrator
from argus.orchestrator.service import now
from argus.research.source_contours import SourceContourResearchPlanner
from argus.security.redaction import safe_error_message
from argus.sources.base import SourceTask
from argus.toolpacks import (
    activate_tool_pack,
    active_tool_pack,
    resolved_tool_pack_from_request,
)


class ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator(
    EvidenceStatusAdaptiveResearchOrchestrator
):
    """Run every collection inside its versioned consumer tool-pack boundary.

    ``contextvars`` keeps the active pack scoped to the current async collection task. The
    SourceRegistry consumes that context both when selecting initial adapters and when a
    later discovery/child task asks for an adapter, so concurrent consumers cannot leak
    source tooling into one another.

    Consumer-specific delivery semantics are also selected from the ToolPack. ARGUS Core
    never branches on consumer IDs: Kraken's broad stream is one policy, while Janus,
    Historical and future consumers can keep different result policies.

    For source-family policies such as ``urban_signals`` the seven public-source contours
    are executed as strict serial research lanes. One lane completes its bounded
    DISCOVER -> FETCH -> EXTRACT -> NORMALIZE -> EVIDENCE/PROVENANCE -> COMMIT cycle before
    discovery for the next lane begins. Public map providers then run in the same serial
    manner. This deliberately trades some latency for source diversity, predictable load
    and substantially lower anti-bot pressure on shared search providers.
    """

    tool_pack_execution_contract_version = "consumer-tool-pack/4"
    source_contour_queue_priority_version = "source-contour-queue/3"
    serial_research_lane_version = "serial-research-lanes/1"
    serial_public_map_lane_version = "serial-public-map-lanes/1"

    def __init__(
        self,
        *args,
        consumer_delivery=None,
        source_contour_planner: SourceContourResearchPlanner | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.consumer_delivery = consumer_delivery or ConsumerDeliveryProjector()
        self.source_contour_planner = source_contour_planner or SourceContourResearchPlanner()

    async def _run(self, collection_id: str) -> None:
        record = await self.repository.get_collection(collection_id)
        if record is None:
            await super()._run(collection_id)
            return

        pack = resolved_tool_pack_from_request(record.request)
        if pack is not None:
            record.checkpoint = {
                **record.checkpoint,
                "consumer_execution_contract": {
                    "version": self.tool_pack_execution_contract_version,
                    "consumer_id": record.request.consumer,
                    "consumer_profile_version": record.request.consumer_profile_version,
                    "capability": record.request.capability,
                    "requested_facts": list(record.request.requested_facts),
                    "tool_pack_id": pack.tool_pack_id,
                    "tool_pack_version": pack.version,
                    "planner_policy": pack.planner_policy,
                    "recipe_namespace": pack.recipe_namespace,
                    "extractor_policy": pack.extractor_policy,
                    "result_delivery_policy": pack.result_delivery_policy,
                    "result_dedup_policy": pack.result_dedup_policy,
                    "shared_tools": list(pack.shared_tools),
                    "source_contour_policy": (
                        self.source_contour_planner.version
                        if self.source_contour_planner.supports_policy(pack.planner_policy)
                        else None
                    ),
                    "source_family_execution": self.serial_research_lane_version,
                    "public_map_execution": self.serial_public_map_lane_version,
                    "public_map_direct_navigation_version": getattr(
                        self.public_map_source_planner,
                        "direct_navigation_version",
                        None,
                    ),
                },
            }
            await self.repository.update_collection(record)

        try:
            with activate_tool_pack(pack):
                await super()._run(collection_id)
        finally:
            self.consumer_delivery.release(collection_id)

    async def _discover_uncovered_intents(
        self,
        record,
        pending,
        uncovered_intents,
    ):
        """Finish independent source families before generic/adaptive discovery.

        The previous implementation discovered all seven contours first and only then
        processed their pages. That still produced a burst of search-engine requests. The
        serial executor below completes each contour's bounded factual acquisition before
        it even asks discovery for the next contour. Public map providers are then processed
        one at a time before normal adaptive discovery resumes.
        """

        pending = await self._run_serial_source_contours(record, pending)
        pending = await self._run_serial_public_maps(record, pending)
        return await super()._discover_uncovered_intents(
            record,
            pending,
            uncovered_intents,
        )

    async def _run_serial_source_contours(self, record, pending):
        pack = active_tool_pack()
        if (
            pack is None
            or self.discovery is None
            or not self.source_contour_planner.supports_policy(pack.planner_policy)
            or record.checkpoint.get("source_contours_complete") is True
        ):
            return pending

        plans = self.source_contour_planner.plans(
            record.request,
            planner_policy=pack.planner_policy,
        )
        states_raw = record.checkpoint.get("source_contours")
        states = dict(states_raw) if isinstance(states_raw, dict) else {}
        all_queries = list(record.checkpoint.get("source_contour_queries", []))
        order = [plan.contour_id for plan in plans]
        serial_state = self._serial_state(record, order=order, kind="source_contour")

        for index, plan in enumerate(plans):
            state_raw = states.get(plan.contour_id)
            state = dict(state_raw) if isinstance(state_raw, dict) else {}
            if state.get("processing_complete") is True:
                continue

            lane_id = f"source_contour:{plan.contour_id}"
            lane_tasks, pending = self._take_lane_tasks(pending, lane_id)

            if state.get("attempted") is not True:
                denied_domains = list(
                    dict.fromkeys(
                        [
                            *record.request.constraints.denied_domains,
                            *plan.denied_domain_roots,
                        ]
                    )
                )
                constraints = record.request.constraints.model_copy(
                    update={
                        "max_pages": max(1, int(plan.max_destinations)),
                        "denied_domains": denied_domains,
                    }
                )
                contour_request = record.request.model_copy(update={"constraints": constraints})

                record.stage = f"discovery:source_contour:{plan.contour_id}"
                record.updated_at = now()
                await self.repository.update_collection(record)

                outcome = await self.discovery.discover(list(plan.queries), contour_request)
                lane_tasks = []
                for task in outcome.tasks[: max(1, int(plan.max_destinations))]:
                    task.metadata["source_contour"] = plan.contour_id
                    task.metadata["source_contour_version"] = self.source_contour_planner.version
                    task.metadata["source_contour_description"] = plan.description
                    task.metadata["source_contour_priority"] = plan.priority
                    self._tag_serial_lane(
                        task,
                        lane_id=lane_id,
                        lane_kind="source_contour",
                        lane_label=plan.contour_id,
                    )
                    lane_tasks.append(task)

                for query in plan.queries:
                    if query not in all_queries:
                        all_queries.append(query)

                if lane_tasks:
                    status = "discovered"
                elif outcome.blocked:
                    status = "blocked"
                elif any(error.code != "DISCOVERY_NO_RESULTS" for error in outcome.errors):
                    status = "degraded"
                else:
                    status = "no_results"

                state = {
                    "attempted": True,
                    "processing_complete": False,
                    "status": status,
                    "priority": plan.priority,
                    "description": plan.description,
                    "queries": list(plan.queries),
                    "denied_domain_roots": list(plan.denied_domain_roots),
                    "providers_attempted": list(outcome.providers_attempted),
                    "candidates_seen": outcome.candidates_seen,
                    "valid_destinations": outcome.valid_destinations,
                    "destinations_selected": len(lane_tasks),
                    "blocked": outcome.blocked,
                    "stop_reason": outcome.stop_reason,
                    "error_codes": [error.code for error in outcome.errors],
                }
                states[plan.contour_id] = state
                serial_state["active_lane"] = lane_id
                record.checkpoint = {
                    **record.checkpoint,
                    "source_contour_version": self.source_contour_planner.version,
                    "source_contours": states,
                    "source_contour_queries": all_queries,
                    "source_contour_queue_priority_version": (
                        self.source_contour_queue_priority_version
                    ),
                    "serial_research_lanes": serial_state,
                    "pending_tasks": [
                        self._task_dict(item) for item in [*lane_tasks, *pending]
                    ],
                }
                record.updated_at = now()
                await self.repository.update_collection(record)

            future_lanes = (len(plans) - index - 1) + self._serial_public_map_lane_count(record)
            if lane_tasks:
                latest, stats = await self._process_serial_lane(
                    record,
                    lane_tasks,
                    pending,
                    lane_id=lane_id,
                    lane_kind="source_contour",
                    lane_label=plan.contour_id,
                    page_limit=max(1, int(plan.max_destinations)) + 1,
                    future_lane_count=future_lanes,
                )
                self._adopt_record(record, latest)
                state = dict(states.get(plan.contour_id, state))
                state.update(stats)
                if stats.get("errors_added", 0) or stats.get("blocked_pages", 0):
                    state["status"] = "completed_with_warnings"
                else:
                    state["status"] = "completed"
            state["processing_complete"] = True
            states[plan.contour_id] = state
            serial_state = self._serial_state(record, order=order, kind="source_contour")
            serial_state["active_lane"] = None
            completed = list(serial_state.get("completed_lanes", []))
            if lane_id not in completed:
                completed.append(lane_id)
            serial_state["completed_lanes"] = completed
            serial_state["last_completed_lane"] = lane_id

            record.checkpoint = {
                **record.checkpoint,
                "source_contours": states,
                "source_contour_queries": all_queries,
                "serial_research_lanes": serial_state,
                "pending_tasks": [self._task_dict(item) for item in pending],
            }
            record.stage = f"serial_complete:{lane_id}"
            record.updated_at = now()
            await self.repository.update_collection(record)

            if self._execution_budget_exhausted(record):
                break

        all_complete = bool(plans) and all(
            isinstance(states.get(plan.contour_id), dict)
            and states[plan.contour_id].get("processing_complete") is True
            for plan in plans
        )
        record.checkpoint = {
            **record.checkpoint,
            "source_contour_version": self.source_contour_planner.version,
            "source_contours": states,
            "source_contour_queries": all_queries,
            "source_contours_complete": all_complete,
            "source_contour_queue_priority_version": self.source_contour_queue_priority_version,
            "serial_research_lanes": serial_state,
            "pending_tasks": [self._task_dict(item) for item in pending],
        }
        record.updated_at = now()
        await self.repository.update_collection(record)
        return pending

    async def _run_serial_public_maps(self, record, pending):
        planner = self.public_map_source_planner
        if (
            planner is None
            or self.discovery is None
            or record.checkpoint.get("serial_public_map_complete") is True
        ):
            return pending

        requested = [
            intent for intent in record.request.intents if intent in planner.supported_intents
        ]
        if not requested:
            record.checkpoint = {
                **record.checkpoint,
                "serial_public_map_complete": True,
                "serial_public_map_version": self.serial_public_map_lane_version,
            }
            await self.repository.update_collection(record)
            return pending

        providers = [profile.source_id for profile in planner.sources]
        states_raw = record.checkpoint.get("serial_public_map_lanes")
        states = dict(states_raw) if isinstance(states_raw, dict) else {}
        serial_state = self._serial_state(record, order=providers, kind="public_map")

        for index, provider in enumerate(providers):
            state_raw = states.get(provider)
            state = dict(state_raw) if isinstance(state_raw, dict) else {}
            if state.get("processing_complete") is True:
                continue

            lane_id = f"public_map:{provider}"
            lane_tasks, pending = self._take_lane_tasks(pending, lane_id)
            providers_attempted: list[str] = []
            error_codes: list[str] = []
            stop_reason = "not_started"

            if state.get("attempted") is not True:
                if provider in {"2gis_web", "google_maps_web"}:
                    direct = planner.direct_navigation_tasks(record.request, limit=10)
                    lane_tasks = [
                        task
                        for task in direct
                        if str(task.metadata.get("public_map_provider") or "") == provider
                    ][:1]
                    providers_attempted = ["direct_browser"] if lane_tasks else []
                    stop_reason = "direct_public_navigation" if lane_tasks else "no_direct_task"
                else:
                    committed = await self.repository.list_observations(record.collection_id)
                    queries = planner.queries(
                        record.request,
                        observations=committed,
                        seen_queries=set(record.checkpoint.get("public_map_queries", [])),
                        limit=6,
                    )
                    provider_queries = [
                        query for query in queries if "site:yandex.ru/maps" in query
                    ][:1]
                    if provider_queries:
                        constraints = record.request.constraints.model_copy(
                            update={"max_pages": 2}
                        )
                        map_request = record.request.model_copy(update={"constraints": constraints})
                        outcome = await self.discovery.discover(provider_queries, map_request)
                        lane_tasks = [
                            task
                            for task in outcome.tasks
                            if self._map_provider(task.url) == provider
                        ][:2]
                        providers_attempted = list(outcome.providers_attempted)
                        error_codes = [error.code for error in outcome.errors]
                        stop_reason = outcome.stop_reason
                        seen_queries = list(record.checkpoint.get("public_map_queries", []))
                        for query in provider_queries:
                            if query not in seen_queries:
                                seen_queries.append(query)
                        record.checkpoint = {
                            **record.checkpoint,
                            "public_map_queries": seen_queries,
                        }
                    else:
                        lane_tasks = []
                        stop_reason = "no_provider_query"

                for task in lane_tasks:
                    task.metadata["public_map_provider"] = provider
                    self._tag_serial_lane(
                        task,
                        lane_id=lane_id,
                        lane_kind="public_map",
                        lane_label=provider,
                    )

                state = {
                    "attempted": True,
                    "processing_complete": False,
                    "status": "discovered" if lane_tasks else "no_results",
                    "providers_attempted": providers_attempted,
                    "destinations_selected": len(lane_tasks),
                    "error_codes": error_codes,
                    "stop_reason": stop_reason,
                }
                states[provider] = state
                serial_state["active_lane"] = lane_id
                record.checkpoint = {
                    **record.checkpoint,
                    "serial_public_map_version": self.serial_public_map_lane_version,
                    "serial_public_map_lanes": states,
                    "serial_public_map_state": serial_state,
                    "pending_tasks": [
                        self._task_dict(item) for item in [*lane_tasks, *pending]
                    ],
                }
                record.stage = f"discovery:public_map:{provider}"
                record.updated_at = now()
                await self.repository.update_collection(record)

            if lane_tasks:
                page_limit = 4 if provider == "2gis_web" else 3
                latest, stats = await self._process_serial_lane(
                    record,
                    lane_tasks,
                    pending,
                    lane_id=lane_id,
                    lane_kind="public_map",
                    lane_label=provider,
                    page_limit=page_limit,
                    future_lane_count=len(providers) - index - 1,
                )
                self._adopt_record(record, latest)
                state = dict(states.get(provider, state))
                state.update(stats)
                if stats.get("errors_added", 0) or stats.get("blocked_pages", 0):
                    state["status"] = "completed_with_warnings"
                else:
                    state["status"] = "completed"
            state["processing_complete"] = True
            states[provider] = state
            serial_state = self._serial_state(record, order=providers, kind="public_map")
            serial_state["active_lane"] = None
            completed = list(serial_state.get("completed_lanes", []))
            if lane_id not in completed:
                completed.append(lane_id)
            serial_state["completed_lanes"] = completed
            serial_state["last_completed_lane"] = lane_id

            record.checkpoint = {
                **record.checkpoint,
                "serial_public_map_lanes": states,
                "serial_public_map_state": serial_state,
                "pending_tasks": [self._task_dict(item) for item in pending],
            }
            record.stage = f"serial_complete:{lane_id}"
            record.updated_at = now()
            await self.repository.update_collection(record)

            if self._execution_budget_exhausted(record):
                break

        all_complete = bool(providers) and all(
            isinstance(states.get(provider), dict)
            and states[provider].get("processing_complete") is True
            for provider in providers
        )
        record.checkpoint = {
            **record.checkpoint,
            "serial_public_map_version": self.serial_public_map_lane_version,
            "serial_public_map_lanes": states,
            "serial_public_map_state": serial_state,
            "serial_public_map_complete": all_complete,
            "public_map_direct_navigation_complete": all_complete,
            "public_map_direct_navigation_providers": [
                provider
                for provider in providers
                if provider in {"2gis_web", "google_maps_web"}
            ],
            "public_map_direct_navigation_version": getattr(
                planner,
                "direct_navigation_version",
                None,
            ),
            "pending_tasks": [self._task_dict(item) for item in pending],
        }
        record.updated_at = now()
        await self.repository.update_collection(record)
        return pending

    async def _process_serial_lane(
        self,
        record,
        lane_tasks: list[SourceTask],
        deferred_pending: list[SourceTask],
        *,
        lane_id: str,
        lane_kind: str,
        lane_label: str,
        page_limit: int,
        future_lane_count: int,
    ):
        """Drain one bounded research lane without triggering cross-lane expansion."""

        queue = list(lane_tasks)
        visited = set(record.checkpoint.get("visited", []))
        historical_branch_queries = set(
            record.checkpoint.get("historical_branch_queries", [])
        )
        total_budget = int(record.request.constraints.max_pages)
        time_budget_seconds = float(record.request.constraints.max_duration_seconds)
        budget_started_at = await self._ensure_execution_budget_started(record)
        available_pages = max(0, total_budget - len(visited))
        reserve = min(max(0, int(future_lane_count)), max(0, available_pages - 1))
        lane_budget = min(max(1, int(page_limit)), max(0, available_pages - reserve))
        lane_processed = 0
        errors_before = len(record.errors)
        blocked_pages = 0
        initial_queue_size = len(queue)

        while queue and lane_processed < lane_budget and len(visited) < total_budget:
            if await self._is_cancelled(record.collection_id):
                break
            remaining_seconds = self._remaining_execution_seconds(
                budget_started_at,
                time_budget_seconds,
            )
            if remaining_seconds <= 0:
                break

            task = queue.pop(0)
            task_collection_id = str(task.metadata.get("collection_id") or "").strip()
            if task_collection_id and task_collection_id != record.collection_id:
                raise ValueError("serial task collection_id does not match collection")
            task.metadata["collection_id"] = record.collection_id
            self._tag_serial_lane(
                task,
                lane_id=lane_id,
                lane_kind=lane_kind,
                lane_label=lane_label,
            )
            key = task.dedupe_key
            if key in visited:
                continue

            adapter = self.registry.get(task.source_id)
            coverage = SourceCoverage(
                source_id=task.source_id,
                status="running",
                started_at=now(),
            )
            task_timeout_seconds = min(self.source_task_timeout_seconds, remaining_seconds)
            processed = len(visited)

            try:
                async with asyncio.timeout(task_timeout_seconds):
                    with stage_snapshots() as snapshot_batch:
                        fetched = await adapter.fetch(task)
                        result = await adapter.normalize(
                            await adapter.extract(task, fetched, record.request)
                        )

                        working_record = record.model_copy(deep=True)
                        working_queue = list(queue)
                        working_visited = set(visited)
                        working_record.errors.extend(result.errors)
                        coverage.observations = len(result.observations)
                        coverage.blocked = result.blocked
                        if result.errors:
                            coverage.error_code = result.errors[0].code
                            coverage.error_message = result.errors[0].message
                        if result.blocked:
                            coverage.status = "blocked"
                            blocked_pages += 1
                            self._apply_source_block_circuit_breaker(
                                working_record,
                                working_queue,
                                task,
                                coverage,
                            )
                        elif result.partial:
                            coverage.status = "partial"
                        elif result.errors and not result.observations:
                            coverage.status = "error"
                        else:
                            coverage.status = "ok"

                        queued_keys = {item.dedupe_key for item in working_queue}
                        for child in result.discovered_tasks:
                            self._inherit_serial_lane(child, task)
                            if not self._child_belongs_to_lane(
                                child,
                                lane_kind=lane_kind,
                                lane_label=lane_label,
                            ):
                                continue
                            child.metadata["collection_id"] = working_record.collection_id
                            child_key = child.dedupe_key
                            if child_key in working_visited or child_key in queued_keys:
                                continue
                            queued_keys.add(child_key)
                            working_queue.append(child)

                        coverage.finished_at = now()
                        working_record.coverage.append(coverage)
                        working_visited.add(key)
                        working_record.progress_percent = min(
                            99,
                            int(len(working_visited) / max(1, total_budget) * 100),
                        )
                        working_record.stage = f"serial_collecting:{lane_id}"
                        working_record.updated_at = now()
                        working_record.checkpoint = {
                            **working_record.checkpoint,
                            "visited": sorted(working_visited),
                            "historical_branch_queries": sorted(historical_branch_queries),
                            "serial_active_lane": {
                                "version": self.serial_research_lane_version,
                                "lane_id": lane_id,
                                "lane_kind": lane_kind,
                                "lane_label": lane_label,
                            },
                            "pending_tasks": [
                                self._task_dict(item)
                                for item in [*working_queue, *deferred_pending]
                            ],
                        }
                        task_snapshots = list(snapshot_batch.snapshots)

            except asyncio.CancelledError:
                raise
            except CrawlerRequestSkippedError as exc:
                robots_blocked = exc.robots_txt
                checkpoint_pending = [*queue, *deferred_pending]
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
                    pending=checkpoint_pending,
                    visited=visited,
                    historical_branch_queries=historical_branch_queries,
                    processed=processed,
                    total_budget=total_budget,
                    retryable=not robots_blocked,
                    blocked=robots_blocked,
                )
                if robots_blocked:
                    blocked_pages += 1
                visited.add(key)
                lane_processed += 1
                continue
            except TimeoutError:
                checkpoint_pending = [*queue, *deferred_pending]
                await self._record_task_failure(
                    record,
                    task=task,
                    coverage=coverage,
                    error_code="SOURCE_TASK_TIMEOUT",
                    message=(
                        "Source task exceeded the bounded execution timeout "
                        f"of {self.source_task_timeout_seconds:g} seconds."
                    ),
                    pending=checkpoint_pending,
                    visited=visited,
                    historical_branch_queries=historical_branch_queries,
                    processed=processed,
                    total_budget=total_budget,
                )
                visited.add(key)
                lane_processed += 1
                continue
            except Exception as exc:
                checkpoint_pending = [*queue, *deferred_pending]
                await self._record_task_failure(
                    record,
                    task=task,
                    coverage=coverage,
                    error_code="SOURCE_ERROR",
                    message=safe_error_message(exc, max_length=300),
                    pending=checkpoint_pending,
                    visited=visited,
                    historical_branch_queries=historical_branch_queries,
                    processed=processed,
                    total_budget=total_budget,
                )
                visited.add(key)
                lane_processed += 1
                continue

            await self._commit_task_success(
                working_record,
                observations=result.observations,
                evidence=result.evidence,
                snapshots=task_snapshots,
            )
            record = working_record
            queue = working_queue
            visited = working_visited
            lane_processed += 1

        truncated_tasks = len(queue)
        record.checkpoint = {
            **record.checkpoint,
            "visited": sorted(visited),
            "historical_branch_queries": sorted(historical_branch_queries),
            "serial_active_lane": None,
            "pending_tasks": [self._task_dict(item) for item in deferred_pending],
        }
        record.updated_at = now()
        await self.repository.update_collection(record)
        return record, {
            "serial_lane_version": self.serial_research_lane_version,
            "initial_tasks": initial_queue_size,
            "processed_pages": lane_processed,
            "lane_page_limit": lane_budget,
            "truncated_tasks": truncated_tasks,
            "blocked_pages": blocked_pages,
            "errors_added": max(0, len(record.errors) - errors_before),
        }

    @staticmethod
    def _tag_serial_lane(
        task: SourceTask,
        *,
        lane_id: str,
        lane_kind: str,
        lane_label: str,
    ) -> None:
        task.metadata["serial_research_lane_id"] = lane_id
        task.metadata["serial_research_lane_kind"] = lane_kind
        task.metadata["serial_research_lane_label"] = lane_label
        task.metadata["serial_research_lane_version"] = "serial-research-lanes/1"

    @staticmethod
    def _inherit_serial_lane(child: SourceTask, parent: SourceTask) -> None:
        for key in (
            "serial_research_lane_id",
            "serial_research_lane_kind",
            "serial_research_lane_label",
            "serial_research_lane_version",
            "source_contour",
            "source_contour_version",
            "source_contour_description",
            "source_contour_priority",
            "public_map_provider",
        ):
            if key in parent.metadata:
                child.metadata[key] = parent.metadata[key]

    @staticmethod
    def _map_provider(url: str) -> str | None:
        classification = classify_public_map_url(url)
        if classification is None:
            return None
        provider = str(classification.get("provider") or "").strip()
        return provider or None

    @classmethod
    def _child_belongs_to_lane(
        cls,
        child: SourceTask,
        *,
        lane_kind: str,
        lane_label: str,
    ) -> bool:
        if lane_kind != "public_map":
            return True
        provider = cls._map_provider(child.url)
        return provider == lane_label

    @staticmethod
    def _take_lane_tasks(pending: list[SourceTask], lane_id: str):
        lane_tasks = [
            task
            for task in pending
            if str(task.metadata.get("serial_research_lane_id") or "") == lane_id
        ]
        deferred = [task for task in pending if task not in lane_tasks]
        return lane_tasks, deferred

    def _serial_state(self, record, *, order: list[str], kind: str) -> dict[str, object]:
        key = "serial_research_lanes" if kind == "source_contour" else "serial_public_map_state"
        raw = record.checkpoint.get(key)
        state = dict(raw) if isinstance(raw, dict) else {}
        state.update(
            {
                "version": (
                    self.serial_research_lane_version
                    if kind == "source_contour"
                    else self.serial_public_map_lane_version
                ),
                "kind": kind,
                "strict_sequential": True,
                "order": list(order),
            }
        )
        state.setdefault("active_lane", None)
        state.setdefault("completed_lanes", [])
        return state

    def _serial_public_map_lane_count(self, record) -> int:
        planner = self.public_map_source_planner
        if planner is None:
            return 0
        if not any(intent in planner.supported_intents for intent in record.request.intents):
            return 0
        return len(planner.sources)

    @staticmethod
    def _execution_budget_exhausted(record) -> bool:
        visited = record.checkpoint.get("visited", [])
        return len(visited) >= int(record.request.constraints.max_pages)

    @staticmethod
    def _adopt_record(target, source) -> None:
        for name in (
            "status",
            "partial",
            "progress_percent",
            "stage",
            "updated_at",
            "checkpoint",
            "coverage",
            "errors",
        ):
            setattr(target, name, getattr(source, name))

    async def _discover_direct_public_maps(self, record, pending):
        """Backward-compatible bulk helper retained for external/tests callers.

        Production urban-signal execution uses ``_run_serial_public_maps`` instead.
        """

        if (
            self.public_map_source_planner is None
            or record.checkpoint.get("public_map_direct_navigation_complete") is True
        ):
            return pending

        tasks = self.public_map_source_planner.direct_navigation_tasks(record.request, limit=2)
        pending = self._merge_tasks(pending, tasks, record.collection_id)
        record.checkpoint = {
            **record.checkpoint,
            "public_map_source_version": self.public_map_source_planner.version,
            "public_map_direct_navigation_version": getattr(
                self.public_map_source_planner,
                "direct_navigation_version",
                None,
            ),
            "public_map_direct_navigation_complete": True,
            "public_map_direct_navigation_tasks": len(tasks),
            "public_map_direct_navigation_providers": [
                str(task.metadata.get("public_map_provider") or "")
                for task in tasks
            ],
            "pending_tasks": [self._task_dict(task) for task in pending],
        }
        record.updated_at = now()
        await self.repository.update_collection(record)
        return pending

    async def _discover_source_contours(self, record, pending):
        """Backward-compatible bulk helper retained for tests and diagnostics.

        Production urban-signal execution no longer calls this helper because doing all
        discovery first creates the request burst that strict serial lanes are designed to
        avoid.
        """

        pack = active_tool_pack()
        if (
            pack is None
            or self.discovery is None
            or not self.source_contour_planner.supports_policy(pack.planner_policy)
            or record.checkpoint.get("source_contours_complete") is True
        ):
            return pending

        plans = self.source_contour_planner.plans(
            record.request,
            planner_policy=pack.planner_policy,
        )
        states_raw = record.checkpoint.get("source_contours")
        states = dict(states_raw) if isinstance(states_raw, dict) else {}
        all_queries = list(record.checkpoint.get("source_contour_queries", []))
        queue_version = ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator.source_contour_queue_priority_version

        record.stage = "discovery:source_contours"
        record.updated_at = now()
        await self.repository.update_collection(record)

        for plan in plans:
            previous = states.get(plan.contour_id)
            if isinstance(previous, dict) and previous.get("attempted") is True:
                continue

            denied_domains = list(
                dict.fromkeys(
                    [
                        *record.request.constraints.denied_domains,
                        *plan.denied_domain_roots,
                    ]
                )
            )
            constraints = record.request.constraints.model_copy(
                update={
                    "max_pages": max(1, int(plan.max_destinations)),
                    "denied_domains": denied_domains,
                }
            )
            contour_request = record.request.model_copy(update={"constraints": constraints})
            outcome = await self.discovery.discover(list(plan.queries), contour_request)

            tagged_tasks = []
            for task in outcome.tasks:
                task.metadata["source_contour"] = plan.contour_id
                task.metadata["source_contour_version"] = self.source_contour_planner.version
                task.metadata["source_contour_description"] = plan.description
                task.metadata["source_contour_priority"] = plan.priority
                tagged_tasks.append(task)
            pending = self._merge_tasks(pending, tagged_tasks, record.collection_id)

            for query in plan.queries:
                if query not in all_queries:
                    all_queries.append(query)

            if tagged_tasks:
                status = "discovered"
            elif outcome.blocked:
                status = "blocked"
            elif any(error.code != "DISCOVERY_NO_RESULTS" for error in outcome.errors):
                status = "degraded"
            else:
                status = "no_results"

            states[plan.contour_id] = {
                "attempted": True,
                "status": status,
                "priority": plan.priority,
                "description": plan.description,
                "queries": list(plan.queries),
                "denied_domain_roots": list(plan.denied_domain_roots),
                "providers_attempted": list(outcome.providers_attempted),
                "candidates_seen": outcome.candidates_seen,
                "valid_destinations": outcome.valid_destinations,
                "destinations_selected": len(tagged_tasks),
                "blocked": outcome.blocked,
                "stop_reason": outcome.stop_reason,
                "error_codes": [error.code for error in outcome.errors],
            }
            record.checkpoint = {
                **record.checkpoint,
                "source_contour_version": self.source_contour_planner.version,
                "source_contours": states,
                "source_contour_queries": all_queries,
                "source_contour_queue_priority_version": queue_version,
                "pending_tasks": [self._task_dict(task) for task in pending],
            }
            record.updated_at = now()
            await self.repository.update_collection(record)

        record.checkpoint = {
            **record.checkpoint,
            "source_contour_version": self.source_contour_planner.version,
            "source_contours": states,
            "source_contour_queries": all_queries,
            "source_contours_complete": True,
            "source_contour_queue_priority_version": queue_version,
            "pending_tasks": [self._task_dict(task) for task in pending],
        }
        record.updated_at = now()
        await self.repository.update_collection(record)
        return pending

    @classmethod
    def _pending_priority(
        cls,
        task: SourceTask,
        requested: set[str],
    ) -> tuple[int, int, int, float, str]:
        """Protect spatial, independent-source and map-provider entry lanes from fan-out."""

        base = super()._pending_priority(task, requested)
        if task.source_id == "openstreetmap_overpass" and task.goal in {
            "area_entity_inventory",
            "area_street_inventory",
        }:
            return (-3, base[1], base[2], base[3], base[4])
        if task.metadata.get("source_contour"):
            try:
                contour_priority = int(task.metadata.get("source_contour_priority", 100) or 100)
            except (TypeError, ValueError):
                contour_priority = 100
            return (-2, base[1], contour_priority, base[3], base[4])
        if task.metadata.get("public_map_direct_navigation"):
            return (-1, base[1], base[2], base[3], base[4])
        if classify_public_map_url(task.url) is not None:
            return (0, base[1], base[2], base[3], base[4])
        return base

    @classmethod
    def _focused_branch(cls, task: SourceTask) -> str | None:
        if task.metadata.get("source_contour"):
            return "source_contour"
        if task.metadata.get("public_map_direct_navigation"):
            return "public_map_direct"
        return super()._focused_branch(task)

    async def _commit_task_success(
        self,
        record,
        *,
        observations,
        evidence,
        snapshots,
    ) -> None:
        pack = active_tool_pack()
        projected_observations, projected_evidence, stats = (
            await self.consumer_delivery.project_task_result(
                self.repository,
                collection_id=record.collection_id,
                pack=pack,
                observations=list(observations),
                evidence=list(evidence),
            )
        )
        previous = record.checkpoint.get("consumer_result_delivery")
        previous = previous if isinstance(previous, dict) else {}
        record.checkpoint = {
            **record.checkpoint,
            "consumer_result_delivery": {
                "version": stats.get("version"),
                "policy": stats.get("policy"),
                "dedup_policy": stats.get("dedup_policy"),
                "semantic_filtering_applied": stats.get(
                    "semantic_filtering_applied", False
                ),
                "observations_input": int(previous.get("observations_input", 0) or 0)
                + int(stats.get("observations_input", 0) or 0),
                "observations_output": int(previous.get("observations_output", 0) or 0)
                + int(stats.get("observations_output", 0) or 0),
                "duplicates_collapsed": int(previous.get("duplicates_collapsed", 0) or 0)
                + int(stats.get("duplicates_collapsed", 0) or 0),
            },
        }
        await super()._commit_task_success(
            record,
            observations=projected_observations,
            evidence=projected_evidence,
            snapshots=snapshots,
        )
