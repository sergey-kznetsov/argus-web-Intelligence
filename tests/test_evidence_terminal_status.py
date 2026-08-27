from pathlib import Path

import pytest

from argus.contracts.models import CollectionRequest, CollectionStatus, Observation
from argus.history.snapshots import sha256_text
from argus.orchestrator.evidence_status import EvidenceStatusAdaptiveResearchOrchestrator
from argus.research.planner import HeuristicResearchPlanner
from argus.sources.base import SourceResult, SourceTask
from argus.sources.registry import SourceRegistry
from argus.storage.sqlite import SQLiteRepository


class OnePageAdapter:
    source_id = "one_page"
    intents = {"*"}

    async def discover(self, request):
        del request
        return [SourceTask(source_id=self.source_id, goal="seed", url="https://example.com/page")]

    async def fetch(self, task):
        return task

    async def extract(self, task, fetched, request):
        del fetched
        text = "Пермь, Комсомольский проспект, 27. Публичная страница объекта."
        return SourceResult(
            observations=[
                Observation(
                    collection_id=str(task.metadata["collection_id"]),
                    analysis_id=request.analysis_id,
                    consumer=request.consumer,
                    source=self.source_id,
                    source_kind="web_page",
                    url=task.url,
                    entity_type="document",
                    text=text,
                    content_hash=sha256_text(text),
                    quality={"intent_evidence": {"public_mentions": True}},
                )
            ]
        )

    async def normalize(self, result):
        return result

    async def health(self):
        return {"status": "ok"}


async def run(tmp_path: Path, *, intents: list[str], allow_partial: bool = True):
    repository = SQLiteRepository(tmp_path / "db.sqlite")
    registry = SourceRegistry()
    registry.register(OnePageAdapter())
    orchestrator = EvidenceStatusAdaptiveResearchOrchestrator(
        repository=repository,
        registry=registry,
        planner=HeuristicResearchPlanner(),
        discovery=None,
        followup_planner=None,
        max_followup_rounds=0,
        max_curated_historical_rounds=0,
        max_curated_public_map_rounds=0,
        max_concurrency=1,
    )
    await orchestrator.start()
    accepted = await orchestrator.submit(
        CollectionRequest(
            consumer="test",
            analysis_id="evidence-terminal",
            territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
            intents=intents,
            allow_partial=allow_partial,
            constraints={"max_pages": 5},
        )
    )
    await orchestrator._jobs[accepted.collection_id]
    record = await repository.get_collection(accepted.collection_id)
    await orchestrator.shutdown()
    assert record is not None
    return record


@pytest.mark.asyncio
async def test_completed_requires_all_requested_intents_to_have_factual_coverage(tmp_path: Path):
    record = await run(tmp_path, intents=["public_mentions"])

    assert record.status == CollectionStatus.COMPLETED
    assert record.checkpoint["final_fully_covered"] is True
    assert record.checkpoint["final_uncovered_intents"] == []
    assert record.checkpoint["final_intent_source_counts"] == {"public_mentions": 1}


@pytest.mark.asyncio
async def test_technical_success_becomes_partial_when_requested_intent_is_uncovered(tmp_path: Path):
    record = await run(tmp_path, intents=["public_mentions", "reviews"])

    assert record.status == CollectionStatus.PARTIAL
    assert record.partial is True
    assert record.checkpoint["final_covered_intents"] == ["public_mentions"]
    assert record.checkpoint["final_uncovered_intents"] == ["reviews"]
    assert any(error.code == "RESEARCH_INTENT_COVERAGE_INCOMPLETE" for error in record.errors)


@pytest.mark.asyncio
async def test_incomplete_coverage_fails_when_partial_results_are_forbidden(tmp_path: Path):
    record = await run(
        tmp_path,
        intents=["public_mentions", "reviews"],
        allow_partial=False,
    )

    assert record.status == CollectionStatus.FAILED
    assert record.partial is False
    assert record.checkpoint["final_uncovered_intents"] == ["reviews"]
