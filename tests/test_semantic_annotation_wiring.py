from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.sources.base import SourceResult
from argus.sources.historical_web import HistoricalTimelineWebAdapter
from argus.sources.intent_evidence_web import IntentEvidenceWebAdapter
from argus.sources.public_map_web import PublicMapProvenanceWebAdapter


@pytest.mark.asyncio
async def test_generic_extract_invokes_semantic_annotation(monkeypatch) -> None:
    calls: list[str] = []

    async def base_extract(self, task, fetched, request):
        del self, task, fetched, request
        return SourceResult(observations=[])

    async def finalize(self, task, request, result):
        del self, task, request, result

    class Annotator:
        async def annotate(self, request, result):
            del request
            calls.append("annotate")
            result.partial = True
            return result

    monkeypatch.setattr(HistoricalTimelineWebAdapter, "extract", base_extract)
    monkeypatch.setattr(PublicMapProvenanceWebAdapter, "_semantic_goals", lambda *args: [])
    monkeypatch.setattr(
        PublicMapProvenanceWebAdapter,
        "_should_semantically_escalate",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(IntentEvidenceWebAdapter, "_finalize_recipe_goal_verification", finalize)
    adapter = object.__new__(IntentEvidenceWebAdapter)
    adapter.intent_evidence_classifier = Annotator()
    request = CollectionRequest(
        consumer="test",
        analysis_id="semantic-wiring",
        territory={"city": "Пермь"},
        intents=["complaints"],
    )

    value = await adapter.extract(SimpleNamespace(metadata={}), None, request)

    assert calls == ["annotate"]
    assert value.partial is True
