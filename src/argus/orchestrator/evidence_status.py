from __future__ import annotations

from argus.contracts.models import CollectionStatus, StructuredError
from argus.orchestrator.adaptive_atomic import AdaptiveResearchAtomicCollectionOrchestrator
from argus.orchestrator.service import now
from argus.research.intent_coverage import IntentCoverageEvaluator


class EvidenceStatusAdaptiveResearchOrchestrator(AdaptiveResearchAtomicCollectionOrchestrator):
    """Align terminal collection status with factual requested-intent coverage.

    Source transport/extraction success is not the same as successful research. The
    underlying atomic orchestrator remains responsible for crawling, persistence and
    technical terminal states. This final production layer only downgrades a technically
    completed/partial collection when requested intents lack source-backed factual coverage.
    Existing BLOCKED/FAILED/CANCELLED decisions are never upgraded.
    """

    coverage_status_version = "evidence-aware-terminal-status/1"
    production_source_task_timeout_seconds = 45.0

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
