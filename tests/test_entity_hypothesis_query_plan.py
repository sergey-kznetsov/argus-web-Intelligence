from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest, Observation
from argus.research.entity_hypotheses import EntityHypothesis, OllamaEntityHypothesisExtractor


def _request() -> CollectionRequest:
    return CollectionRequest(
        consumer="entity-query-plan-test",
        analysis_id="entity-query-plan-test",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["historical_context", "public_mentions"],
    )


def _observation() -> Observation:
    return Observation(
        observation_id="observation-1",
        collection_id="collection-1",
        analysis_id="entity-query-plan-test",
        consumer="entity-query-plan-test",
        source="generic_web",
        source_kind="web_page",
        url="https://example.org/history",
        entity_type="document",
        title="История",
        text="До реконструкции здесь работала гостиница Прикамье.",
        content_hash="a" * 64,
    )


@pytest.mark.asyncio
async def test_propose_queries_matches_orchestrator_contract_without_creating_evidence():
    extractor = OllamaEntityHypothesisExtractor(Settings(browser_serp_enabled=False))
    hypothesis = EntityHypothesis(
        entity_type="organization",
        label="гостиница Прикамье",
        excerpt="До реконструкции здесь работала гостиница Прикамье.",
        source_url="https://example.org/history",
        observation_id="observation-1",
    )
    extractor.extract = AsyncMock(return_value=[hypothesis])

    plan = await extractor.propose_queries(
        _request(),
        [_observation()],
        seen_queries=set(),
        max_queries=2,
    )

    assert plan.version == extractor.query_plan_version
    assert len(plan.queries) == 1
    assert "гостиница Прикамье" in plan.queries[0]
    assert "Комсомольский проспект, 27" in plan.queries[0]
    assert plan.hypotheses == (hypothesis,)
    assert hypothesis.is_evidence is False
