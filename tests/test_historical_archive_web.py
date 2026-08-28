from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation
from argus.research.intent_coverage import IntentCoverageEvaluator
from argus.sources.base import SourceResult, SourceTask
from argus.sources.intent_evidence_web import IntentEvidenceWebAdapter


def _request() -> CollectionRequest:
    return CollectionRequest(
        consumer="historical-archive-web-test",
        analysis_id="historical-archive-web",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["historical_context"],
        constraints={"max_pages": 10, "max_depth": 1, "language": "ru"},
    )


def _task() -> SourceTask:
    return SourceTask(
        source_id="generic_web",
        goal="historical_context",
        url=(
            "https://web.archive.org/web/20160102030405id_/"
            "https://example.org/perm-building"
        ),
        metadata={
            "discovery_provider": "wayback_cdx",
            "archive_original_url": "https://example.org/perm-building",
            "archive_timestamp": "20160102030405",
            "research_goals": ["historical_context"],
        },
    )


def _result(text: str) -> SourceResult:
    observation = Observation(
        observation_id="archive-observation",
        collection_id="collection",
        analysis_id="historical-archive-web",
        consumer="historical-archive-web-test",
        source="generic_web",
        source_kind="web_page",
        url=_task().url,
        entity_type="document",
        title="Архивная страница",
        text=text,
        content_hash="a" * 64,
        provenance={"snapshot_id": "snapshot"},
        quality={"evidence_backed": True},
    )
    evidence = Evidence(
        evidence_id="archive-evidence",
        observation_id=observation.observation_id,
        type="document",
        text=text,
        source=EvidenceSource(
            provider="generic_web",
            url=observation.url,
            collected_at=observation.collected_at,
            source_id="generic_web",
        ),
    )
    return SourceResult(observations=[observation], evidence=[evidence])


def test_fetched_territory_backed_wayback_page_becomes_historical_evidence():
    request = _request()
    result = _result(
        "Пермь, Комсомольский проспект, 27. История здания и его реконструкция."
    )

    IntentEvidenceWebAdapter._attach_historical_archive_provenance(
        _task(), request, result
    )

    observation = result.observations[0]
    assert observation.source_kind == "historical_page_version"
    assert observation.provenance["archive"]["historical_capture"] is True
    assert observation.provenance["archive"]["provider"] == "wayback_cdx"
    assert observation.provenance["archive"]["original_url"] == (
        "https://example.org/perm-building"
    )
    assert observation.provenance["archive"]["capture_timestamp"] == "20160102030405"
    assert observation.quality["historical_territory_relevant"] is True
    assert result.evidence[0].metadata["archive"]["historical_capture"] is True
    assert IntentCoverageEvaluator().supports(
        observation,
        "historical_context",
        request=request,
    )


def test_unrelated_wayback_page_is_not_promoted_to_historical_context():
    request = _request()
    result = _result(
        "Москва, Октябрьская площадь. История городской площади и старые здания."
    )

    IntentEvidenceWebAdapter._attach_historical_archive_provenance(
        _task(), request, result
    )

    observation = result.observations[0]
    assert observation.source_kind == "web_page"
    assert "archive" not in observation.provenance
    assert "archive" not in result.evidence[0].metadata
    assert not IntentCoverageEvaluator().supports(
        observation,
        "historical_context",
        request=request,
    )
