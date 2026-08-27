from __future__ import annotations

import asyncio

from argus.contracts.models import CollectionStatus, StructuredError
from argus.orchestrator.adaptive_atomic import AdaptiveResearchAtomicCollectionOrchestrator
from argus.orchestrator.service import now
from argus.research.intent_coverage import IntentCoverageEvaluator
from argus.security.redaction import safe_error_message


class EvidenceStatusAdaptiveResearchOrchestrator(AdaptiveResearchAtomicCollectionOrchestrator):
    """Align terminal status, supervision and queue policy with factual coverage."""

    coverage_status_version = "evidence-aware-terminal-status/1"
    research_supervision_version = "factual-gap-supervision/1"
    research_queue_priority_version = "research-queue-priority/3"
    production_source_task_timeout_seconds = 45.0
    research_expansion_timeout_seconds = 20.0
    research_supervisor_timeout_seconds = 5.0
    research_supervisor_interval_pages = 4
    max_factual_pending_for_followup = 2

    def __init__(
        self,
        *args,
        intent_coverage: IntentCoverageEvaluator | None = None,
        **kwargs,
    ) -> None:
        kwargs.setdefault(
            "source_task_timeout_seconds",
            self.production_source_task_timeout_seconds,
        )
        super().__init__(*args, **kwargs)
        self.intent_coverage = intent_coverage or IntentCoverageEvaluator()

    async def _process_tasks(self, record, pending) -> None:
        await super()._process_tasks(record, pending)
        await self._apply_evidence_aware_terminal_status(record.collection_id)

    async def _expand_historical(
        self,
        record,
        task,
        observations,
        pending,
        visited,
        seen_queries,
    ) -> None:
        """Supervise first, then bound optional expansion without discarding facts."""

        await self._refresh_research_supervisor(
            record,
            observations=observations,
            pending=pending,
            visited=visited,
        )
        try:
            async with asyncio.timeout(self.research_expansion_timeout_seconds):
                await super()._expand_historical(
                    record,
                    task,
                    observations,
                    pending,
                    visited,
                    seen_queries,
                )
        except TimeoutError:
            count = int(record.checkpoint.get("research_expansion_timeout_count", 0) or 0) + 1
            record.checkpoint = {
                **record.checkpoint,
                "research_expansion_timeout_count": count,
                "research_expansion_timeout_seconds": self.research_expansion_timeout_seconds,
            }
            if not any(error.code == "RESEARCH_EXPANSION_TIMEOUT" for error in record.errors):
                record.errors.append(
                    StructuredError(
                        code="RESEARCH_EXPANSION_TIMEOUT",
                        message=(
                            "Optional research branch expansion exceeded its bounded "
                            f"timeout of {self.research_expansion_timeout_seconds:g} seconds; "
                            "already extracted factual results are retained."
                        ),
                        retryable=True,
                        source_id=task.source_id,
                    )
                )

    async def _refresh_research_supervisor(
        self,
        record,
        *,
        observations,
        pending,
        visited,
    ) -> dict[str, object]:
        """Record deterministic gap supervision before optional model guidance.

        The deterministic preflight is authoritative for coverage and is always available,
        even when the local model is slow or unavailable. The model may add bounded navigation
        hints, but it cannot remove factual gaps or become Evidence.
        """

        committed = await self.repository.list_observations(record.collection_id)
        all_observations = [*committed, *observations]
        counts = self.intent_coverage.counts(all_observations, request=record.request)
        requested = self._requested_intents(record.request.intents)
        target = max(
            1,
            int(getattr(self.research_supervisor, "target_sources_per_intent", 2) or 2),
        )
        priority_intents = [
            intent for intent in requested if int(counts.get(intent, 0)) < target
        ]
        remaining_page_budget = self._remaining_execution_budget(record, visited)
        factual_pending = self._factual_pending_count(pending)
        previous = record.checkpoint.get("research_supervisor")
        previous = previous if isinstance(previous, dict) else {}
        last_model = previous.get("last_model_decision")
        last_model = last_model if isinstance(last_model, dict) else None

        flags: list[str] = []
        if priority_intents:
            flags.append("coverage_gap")
        if pending:
            flags.append("pending_work_present")
        if factual_pending > self.max_factual_pending_for_followup:
            flags.append("followup_backpressure")
        if remaining_page_budget <= 2:
            flags.append("budget_low")

        payload: dict[str, object] = {
            "version": self.research_supervision_version,
            "continue_research": bool(priority_intents and remaining_page_budget > 0),
            "priority_intents": priority_intents,
            "query_hints": list(last_model.get("query_hints", [])) if last_model else [],
            "flags": flags,
            "rationale_ru": (
                "Остались незакрытые фактические цели; исследование продолжается в пределах "
                "бюджета."
                if priority_intents and remaining_page_budget > 0
                else "Фактические цели закрыты либо доступный бюджет исчерпан."
            ),
            "model_assisted": bool(last_model),
            "model_output_is_evidence": False,
            "factual_coverage_counts": {
                intent: int(counts.get(intent, 0)) for intent in requested
            },
            "target_sources_per_intent": target,
            "pending_tasks": len(pending),
            "factual_pending_tasks": factual_pending,
            "remaining_page_budget": remaining_page_budget,
            "processed_pages": len(visited),
        }
        if last_model:
            payload["last_model_decision"] = last_model
        record.checkpoint = {
            **record.checkpoint,
            "research_supervisor": payload,
        }

        if self.research_supervisor is None or not payload["continue_research"]:
            return payload

        processed_pages = len(visited)
        raw_last_attempt = record.checkpoint.get(
            "research_supervisor_last_attempt_processed_pages"
        )
        try:
            last_attempt = int(raw_last_attempt)
        except (TypeError, ValueError):
            last_attempt = -self.research_supervisor_interval_pages
        should_assess = (
            processed_pages - last_attempt >= self.research_supervisor_interval_pages
            or factual_pending <= self.max_factual_pending_for_followup
        )
        if not should_assess:
            return payload

        seen = self._seen_research_queries(record)
        record.checkpoint = {
            **record.checkpoint,
            "research_supervisor_last_attempt_processed_pages": processed_pages,
        }
        try:
            async with asyncio.timeout(self.research_supervisor_timeout_seconds):
                decision = await self.research_supervisor.assess(
                    record.request,
                    all_observations,
                    errors=list(record.errors),
                    seen_queries=seen,
                    pending_count=len(pending),
                    remaining_page_budget=remaining_page_budget,
                )
        except TimeoutError:
            payload["flags"] = [*flags, "model_timeout"]
            payload["model_timeout"] = True
            payload["model_timeout_seconds"] = self.research_supervisor_timeout_seconds
            record.checkpoint = {
                **record.checkpoint,
                "research_supervisor": payload,
            }
            return payload
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            payload["flags"] = [*flags, "model_error"]
            payload["model_error"] = safe_error_message(exc, max_length=240)
            record.checkpoint = {
                **record.checkpoint,
                "research_supervisor": payload,
            }
            return payload

        model_decision = decision.as_dict()
        payload.update(
            {
                "query_hints": list(model_decision.get("query_hints", [])),
                "flags": list(dict.fromkeys([*flags, *model_decision.get("flags", [])])),
                "rationale_ru": str(model_decision.get("rationale_ru") or payload["rationale_ru"]),
                "model_assisted": bool(model_decision.get("model_assisted")),
                "last_model_decision": model_decision,
                "last_model_processed_pages": processed_pages,
            }
        )
        # Deterministic factual gaps remain authoritative. The model can guide navigation,
        # but cannot stop research or erase an uncovered requested intent.
        payload["continue_research"] = bool(priority_intents and remaining_page_budget > 0)
        payload["priority_intents"] = priority_intents
        record.checkpoint = {
            **record.checkpoint,
            "research_supervisor": payload,
        }
        return payload

    async def _expand_research_gaps(
        self,
        record,
        task,
        observations,
        pending,
        visited,
    ) -> None:
        """Generate new follow-up work only after factual queue backpressure clears."""

        if (
            self.discovery is None
            or self.followup_planner is None
            or self.max_followup_rounds <= 0
        ):
            return
        round_count = int(record.checkpoint.get("adaptive_followup_rounds", 0) or 0)
        if round_count >= self.max_followup_rounds:
            return
        remaining_page_budget = self._remaining_execution_budget(record, visited)
        if remaining_page_budget <= 0:
            return

        supervisor = record.checkpoint.get("research_supervisor")
        if not isinstance(supervisor, dict):
            supervisor = await self._refresh_research_supervisor(
                record,
                observations=observations,
                pending=pending,
                visited=visited,
            )
        if not bool(supervisor.get("continue_research")):
            record.checkpoint = {
                **record.checkpoint,
                "adaptive_followup_complete": True,
            }
            return

        factual_pending = self._factual_pending_count(pending)
        record.checkpoint = {
            **record.checkpoint,
            "adaptive_followup_backpressure": {
                "version": "factual-pending-backpressure/1",
                "pending_tasks": len(pending),
                "factual_pending_tasks": factual_pending,
                "support_pending_tasks": len(pending) - factual_pending,
                "max_factual_pending_for_followup": self.max_factual_pending_for_followup,
                "blocked": factual_pending > self.max_factual_pending_for_followup,
            },
        }
        if factual_pending > self.max_factual_pending_for_followup:
            return

        committed = await self.repository.list_observations(record.collection_id)
        all_observations = [*committed, *observations]
        seen = self._seen_research_queries(record)
        max_queries = min(6, remaining_page_budget)
        priority_intents = [
            str(value).strip()
            for value in supervisor.get("priority_intents", [])
            if str(value).strip()
        ]
        uncovered_intents = priority_intents or list(record.request.intents)

        entity_queries: list[str] = []
        if self.entity_hypothesis_extractor is not None:
            hypothesis_plan = await self.entity_hypothesis_extractor.propose_queries(
                record.request,
                all_observations,
                seen_queries=seen,
                max_queries=max_queries,
            )
            entity_queries = list(hypothesis_plan.queries)
            record.checkpoint = {
                **record.checkpoint,
                "entity_hypothesis_version": hypothesis_plan.version,
                "entity_hypothesis_queries": list(entity_queries),
                "entity_hypothesis_model_output_is_evidence": False,
            }

        supervisor_queries = [
            str(value).strip()
            for value in supervisor.get("query_hints", [])
            if str(value).strip()
        ]
        followup_request = record.request.model_copy(update={"intents": uncovered_intents})
        plan = await self.followup_planner.plan_followups(
            followup_request,
            all_observations,
            seen_queries=seen,
            max_queries=max_queries,
        )
        queries = self._merge_followup_queries(
            [*entity_queries, *supervisor_queries],
            plan.queries,
            seen_queries=seen,
            limit=max_queries,
        )
        notes = [
            *plan.notes,
            "research_supervisor:"
            f"{supervisor.get('version', self.research_supervision_version)}:"
            f"model_assisted={str(bool(supervisor.get('model_assisted'))).lower()}",
        ]
        if entity_queries:
            notes.append(
                "entity_hypotheses:"
                f"{self.entity_hypothesis_extractor.version}:queries={len(entity_queries)}"
            )
        if not queries:
            record.checkpoint = {
                **record.checkpoint,
                "adaptive_followup_complete": True,
                "adaptive_followup_notes": notes,
            }
            return

        seen.update(queries)
        constraints = followup_request.constraints.model_copy(
            update={"max_pages": remaining_page_budget}
        )
        branch_request = followup_request.model_copy(update={"constraints": constraints})
        outcome = await self.discovery.discover(queries, branch_request)
        additions = []
        for branch_task in outcome.tasks[:remaining_page_budget]:
            if branch_task.dedupe_key in visited:
                continue
            branch_task.metadata["adaptive_followup_round"] = round_count + 1
            branch_task.metadata["adaptive_followup_from"] = task.url
            branch_task.metadata["adaptive_followup_queries"] = list(queries)
            branch_task.metadata["research_supervisor_version"] = str(
                supervisor.get("version", self.research_supervision_version)
            )
            branch_task.metadata["research_supervisor_model_assisted"] = bool(
                supervisor.get("model_assisted")
            )
            branch_task.metadata["research_supervisor_is_evidence"] = False
            if entity_queries:
                branch_task.metadata["entity_hypothesis_navigation"] = True
                branch_task.metadata["entity_hypothesis_is_evidence"] = False
            additions.append(branch_task)
        self._merge_tasks(pending, additions, record.collection_id)
        record.checkpoint = {
            **record.checkpoint,
            "adaptive_followup_rounds": round_count + 1,
            "adaptive_followup_queries": sorted(seen),
            "adaptive_followup_notes": notes,
            "adaptive_followup_last_candidates": len(additions),
            "execution_budget_version": self.execution_budget_version,
        }

    def _prioritize_pending(self, record, pending) -> None:
        """Prefer currently uncovered factual goals and defer support-only site discovery."""

        if not pending:
            return
        supervisor = record.checkpoint.get("research_supervisor")
        priority_intents = (
            supervisor.get("priority_intents", []) if isinstance(supervisor, dict) else []
        )
        requested = {
            str(intent).strip().casefold()
            for intent in (priority_intents or record.request.intents)
            if str(intent).strip()
        }
        pending.sort(key=lambda item: self._pending_priority(item, requested))
        record.checkpoint = {
            **record.checkpoint,
            "research_queue_priority_version": self.research_queue_priority_version,
            "research_queue_candidate_count": len(pending),
            "research_queue_next": [
                {
                    "source_id": item.source_id,
                    "goal": item.goal,
                    "depth": item.depth,
                    "focused_branch": self._focused_branch(item),
                }
                for item in pending[:5]
            ],
        }

    @classmethod
    def _pending_priority(cls, task, requested):
        base = super()._pending_priority(task, requested)
        if task.source_id == "site_discovery" and cls._focused_branch(task) is None:
            return (10, *base[1:])
        return base

    @staticmethod
    def _factual_pending_count(pending) -> int:
        return sum(1 for item in pending if item.source_id != "site_discovery")

    @staticmethod
    def _seen_research_queries(record) -> set[str]:
        return {
            str(query)
            for bucket in (
                record.checkpoint.get("queries", []),
                record.checkpoint.get("discovery_queries", []),
                record.checkpoint.get("adaptive_followup_queries", []),
            )
            if isinstance(bucket, list)
            for query in bucket
            if isinstance(query, str) and query.strip()
        }

    async def _apply_evidence_aware_terminal_status(self, collection_id: str) -> None:
        record = await self.repository.get_collection(collection_id)
        if record is None or record.status in {
            CollectionStatus.CANCELLED,
            CollectionStatus.BLOCKED,
            CollectionStatus.FAILED,
        }:
            return

        requested = self._requested_intents(record.request.intents)
        if not requested:
            return
        observations = await self.repository.list_observations(collection_id)
        counts = self.intent_coverage.counts(observations, request=record.request)
        intent_counts = {intent: int(counts.get(intent, 0)) for intent in requested}
        covered = [intent for intent in requested if intent_counts[intent] > 0]
        uncovered = [intent for intent in requested if intent_counts[intent] == 0]

        record.checkpoint = {
            **record.checkpoint,
            "final_intent_coverage_version": self.intent_coverage.version,
            "final_terminal_status_version": self.coverage_status_version,
            "final_intent_source_counts": intent_counts,
            "final_covered_intents": covered,
            "final_uncovered_intents": uncovered,
            "final_fully_covered": not uncovered,
        }
        if uncovered:
            self._append_coverage_error(record, uncovered)
            if record.request.allow_partial and observations:
                record.status = CollectionStatus.PARTIAL
                record.partial = True
            else:
                record.status = CollectionStatus.FAILED
                record.partial = False
            record.stage = record.status.value
        record.updated_at = now()
        await self.repository.update_collection(record)

    @staticmethod
    def _requested_intents(values) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            intent = str(raw).strip().casefold()
            if not intent or intent in seen:
                continue
            seen.add(intent)
            result.append(intent)
        return result

    @staticmethod
    def _append_coverage_error(record, uncovered: list[str]) -> None:
        if any(error.code == "RESEARCH_INTENT_COVERAGE_INCOMPLETE" for error in record.errors):
            return
        record.errors.append(
            StructuredError(
                code="RESEARCH_INTENT_COVERAGE_INCOMPLETE",
                message=(
                    "Collection finished without factual source coverage for requested intents: "
                    + ", ".join(uncovered)
                ),
                retryable=False,
                source_id="research_coverage",
            )
        )
