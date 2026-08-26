from __future__ import annotations

from argus.contracts.models import Evidence, EvidenceSource, Observation, utcnow
from argus.crawler.models import FetchResult
from argus.sources.base import SourceResult, SourceTask
from argus.sources.public_map_web import PublicMapProvenanceWebAdapter


def adapter_with_agent() -> PublicMapProvenanceWebAdapter:
    adapter = object.__new__(PublicMapProvenanceWebAdapter)
    adapter.agent = object()
    return adapter


def task(*goals: str) -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal=goals[0] if goals else "reviews",
        url="https://2gis.ru/izhevsk/firm/example",
        metadata={"research_goals": list(goals or ("reviews",))},
    )


def fetched(url: str = "https://2gis.ru/izhevsk/firm/example") -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status_code=200,
        content_type="text/html",
        text="<html><body>interactive map shell</body></html>",
        runtime="browser",
    )


def observation(entity_type: str) -> Observation:
    return Observation(
        observation_id=f"obs-{entity_type}",
        collection_id="collection-1",
        analysis_id="analysis-1",
        consumer="test",
        source="generic_web",
        source_kind="web_page" if entity_type == "document" else "json_ld",
        url="https://2gis.ru/izhevsk/firm/example",
        entity_type=entity_type,
        text="public content",
        data={},
        content_hash="a" * 64,
        provenance={},
        quality={},
    )


def test_public_map_review_goal_escalates_when_browser_has_no_review_fact():
    adapter = adapter_with_agent()
    result = SourceResult(observations=[observation("document")])

    assert adapter._should_semantically_escalate(task("reviews"), fetched(), result) is True


def test_public_map_review_goal_does_not_escalate_after_source_declared_review():
    adapter = adapter_with_agent()
    result = SourceResult(
        observations=[observation("document"), observation("review")]
    )

    assert adapter._should_semantically_escalate(task("reviews"), fetched(), result) is False


def test_public_map_review_goal_accepts_exact_excerpt_semantic_evidence():
    adapter = adapter_with_agent()
    item = observation("document")
    item.quality["intent_evidence"] = {"reviews": True}
    result = SourceResult(observations=[item])

    assert adapter._semantic_goal_fact_count(result, ["reviews"]) == 1
    assert adapter._should_semantically_escalate(task("reviews"), fetched(), result) is False


def test_public_map_non_review_semantic_goal_uses_same_coverage_evaluator():
    adapter = adapter_with_agent()
    item = observation("document")
    item.quality["intent_evidence"] = {"complaints": True}
    result = SourceResult(observations=[item])

    assert adapter._semantic_goals(task("complaints")) == ["complaints"]
    assert adapter._semantic_goal_fact_count(result, ["complaints"]) == 1
    assert adapter._should_semantically_escalate(task("complaints"), fetched(), result) is False


def test_public_map_semantic_escalation_is_not_used_for_unrelated_web_pages():
    adapter = adapter_with_agent()
    result = SourceResult(observations=[observation("document")])

    assert adapter._should_semantically_escalate(
        task("reviews"),
        fetched("https://example.org/place"),
        result,
    ) is False


def test_public_map_semantic_escalation_provenance_never_treats_agent_output_as_evidence():
    adapter = adapter_with_agent()
    source = EvidenceSource(
        provider="generic_web",
        url="https://2gis.ru/izhevsk/firm/example",
        collected_at=utcnow(),
        source_id="generic_web",
    )
    item = observation("document")
    evidence = Evidence(
        evidence_id="ev-1",
        observation_id=item.observation_id,
        type="document",
        text="public content",
        source=source,
        metadata={},
    )
    result = SourceResult(observations=[item], evidence=[evidence])
    source_task = task("reviews")
    source_task.metadata.update(
        {
            "semantic_agent_retry_attempted": True,
            "semantic_agent_retry_accepted": True,
            "semantic_agent_retry_reason": "review_goal_without_review_fact",
            "semantic_agent_retry_goals": ["reviews"],
        }
    )

    adapter._attach_public_map_provenance(result, source_task)

    metadata = item.provenance["public_map_semantic_escalation"]
    assert metadata["attempted"] is True
    assert metadata["accepted"] is True
    assert metadata["goals"] == ["reviews"]
    assert metadata["agent_output_is_evidence"] is False
    assert evidence.metadata["public_map_semantic_escalation"]["agent_output_is_evidence"] is False
