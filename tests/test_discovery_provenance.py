from __future__ import annotations

from argus.contracts.models import Evidence, EvidenceSource, Observation, utcnow
from argus.sources.base import SourceResult, SourceTask
from argus.sources.duplicate_web import DuplicateAwareWebAdapter


def test_discovery_navigation_metadata_is_provenance_not_evidence_confidence():
    observation = Observation(
        observation_id="obs-navigation",
        collection_id="collection-navigation",
        analysis_id="analysis-navigation",
        consumer="test",
        source="generic_web",
        source_kind="web_page",
        url="https://example.com/article",
        entity_type="document",
        text="factual text",
        content_hash="a" * 64,
        provenance={"discovery": {"provider": "old-provider-field"}},
    )
    evidence = Evidence(
        evidence_id="ev-navigation",
        observation_id=observation.observation_id,
        type="document",
        text="factual text",
        source=EvidenceSource(
            provider="generic_web",
            url=observation.url,
            collected_at=utcnow(),
            source_id="generic_web",
        ),
    )
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url=observation.url,
        metadata={
            "discovery_provider": "searxng",
            "discovery_rank": 2,
            "discovery_query": '"Ижевск, Пушкинская, 277" упоминания',
            "discovery_original_url": "https://Example.com:443/article?utm_source=x",
            "discovery_canonical_url": observation.url,
            "discovery_navigation_score": 75,
            "discovery_ranking_components": {
                "domain_priority": 0,
                "provider_rank": 2,
                "locality_matches": 1,
                "https": True,
            },
            "discovery_ranking_version": "discovery-ranking/1",
            "discovery_telemetry_version": "discovery-telemetry/2",
            "discovery_stop_policy": "first_provider_with_valid_destinations",
            "discovery_task_budget": 30,
        },
    )
    result = SourceResult(observations=[observation], evidence=[evidence])

    DuplicateAwareWebAdapter._attach_discovery_navigation_provenance(result, task)

    discovery = observation.provenance["discovery"]
    assert discovery["provider"] == "searxng"
    assert discovery["rank"] == 2
    assert discovery["query"] == '"Ижевск, Пушкинская, 277" упоминания'
    assert discovery["canonical_url"] == observation.url
    assert discovery["navigation_score"] == 75
    assert discovery["navigation_only"] is True
    assert discovery["is_evidence"] is False
    assert "confidence" not in discovery
    assert evidence.metadata["discovery_navigation"]["query"] == discovery["query"]
    assert evidence.metadata["discovery_navigation"]["navigation_only"] is True
    assert evidence.metadata["discovery_navigation"]["is_evidence"] is False
