from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest, Observation, Point
from argus.research.intent_evidence import OllamaIntentEvidenceClassifier
from argus.research.source_scoped_intents import SourceScopedIntentEvidenceClassifier
from argus.sources.base import SourceResult


class _Classifier:
    version = "delegate/1"
    supported_intents = {"*"}
    marker_required_intents = set()

    def __init__(self) -> None:
        self.received: list[str] | None = None
        self.settings = SimpleNamespace(llm_required=True)

    async def annotate(self, request, result):
        self.received = list(request.intents)
        return result


def _request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="source-scope-test",
        analysis_id="a1",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=list(intents),
    )


def _urban_request(
    *intents: str,
    territory: dict[str, object] | None = None,
) -> CollectionRequest:
    return CollectionRequest(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban_signals",
        analysis_id="urban-signal-analysis",
        territory=territory
        or {
            "city": "Ижевск",
            "address": "Пушкинская, 277",
            "metadata": {"street": "Пушкинская"},
        },
        intents=list(intents),
    )


def _observation(
    *,
    observation_id: str,
    text: str | None,
    url: str = "https://2gis.ru/izhevsk/firm/example/tab/reviews",
    entity_type: str = "review",
    geo: Point | None = None,
    source_kind: str = "web_page",
    provenance: dict[str, object] | None = None,
) -> Observation:
    return Observation(
        observation_id=observation_id,
        collection_id="urban-signal-collection",
        analysis_id="urban-signal-analysis",
        consumer="kraken.development.uds",
        source="generic_web",
        source_kind=source_kind,
        url=url,
        entity_type=entity_type,
        text=text,
        geo=geo,
        content_hash=(observation_id[:1] or "a") * 64,
        provenance=provenance or {},
        quality={},
    )


def _urban_classifier() -> SourceScopedIntentEvidenceClassifier:
    settings = Settings(
        llm_required=False,
        ollama_url="http://127.0.0.1:9",
    )
    return SourceScopedIntentEvidenceClassifier(
        OllamaIntentEvidenceClassifier(settings),
        source_scoped_intents={"residential_population", "residential_premises_count"},
    )


@pytest.mark.asyncio
async def test_generic_classifier_never_receives_residential_only_intents():
    delegate = _Classifier()
    classifier = SourceScopedIntentEvidenceClassifier(
        delegate,
        source_scoped_intents={"residential_population", "residential_premises_count"},
    )
    result = SourceResult(observations=[])

    returned = await classifier.annotate(
        _request("residential_population", "residential_premises_count"),
        result,
    )

    assert returned is result
    assert delegate.received is None


@pytest.mark.asyncio
async def test_mixed_request_passes_only_unscoped_intents_to_generic_classifier():
    delegate = _Classifier()
    classifier = SourceScopedIntentEvidenceClassifier(
        delegate,
        source_scoped_intents={"residential_population", "residential_premises_count"},
    )

    await classifier.annotate(
        _request("residential_population", "local_news", "complaints"),
        SourceResult(observations=[]),
    )

    assert delegate.received == ["local_news", "complaints"]
    assert classifier.supported_intents == {"*"}


@pytest.mark.asyncio
async def test_keyless_urban_problem_is_exact_source_backed_complaint():
    complaint = "Мне на перекрестке под машину бросаться или что?"
    observation = _observation(
        observation_id="urban-problem",
        text=f"Ижевск, Пушкинская, 277. {complaint}",
    )
    result = SourceResult(observations=[observation])

    returned = await _urban_classifier().annotate(
        _urban_request("complaints", "comments"),
        result,
    )

    assert returned is result
    assert observation.quality["intent_evidence"]["complaints"] is True
    assert "reviews" not in observation.quality["intent_evidence"]
    excerpts = [
        item
        for item in result.evidence
        if item.type == "semantic_intent_excerpt"
        and item.metadata.get("intent") == "complaints"
    ]
    assert len(excerpts) == 1
    assert excerpts[0].text == complaint
    assert excerpts[0].metadata["deterministic_rule_based"] is True
    assert excerpts[0].metadata["semantic_label_model_assisted"] is False


@pytest.mark.asyncio
async def test_establishment_service_review_is_not_promoted_to_urban_signal():
    observation = _observation(
        observation_id="business-review",
        text=(
            "Ижевск, Пушкинская, 277. "
            "Сломанные джойстики, администратор не отвечает и обслуживание плохое."
        ),
    )
    result = SourceResult(observations=[observation])

    await _urban_classifier().annotate(
        _urban_request("complaints", "comments", "discussions"),
        result,
    )

    assert observation.quality.get("intent_evidence") in (None, {})
    assert result.evidence == []


@pytest.mark.asyncio
async def test_public_map_review_can_supply_social_problem_but_never_review_fact():
    problem = "Во дворе разбит тротуар, пешеходам опасно идти к остановке."
    observation = _observation(
        observation_id="review-with-urban-problem",
        text=f"Ижевск, Пушкинская, 277. {problem}",
        entity_type="review",
    )
    result = SourceResult(observations=[observation])

    await _urban_classifier().annotate(
        _urban_request("complaints", "resident_messages"),
        result,
    )

    intent_evidence = observation.quality["intent_evidence"]
    assert intent_evidence["complaints"] is True
    assert "reviews" not in intent_evidence
    assert all(item.metadata.get("intent") != "reviews" for item in result.evidence)


@pytest.mark.asyncio
async def test_unique_same_page_declared_geo_can_prove_radius_for_dynamic_ugc():
    url = "https://2gis.ru/izhevsk/firm/example/tab/reviews"
    problem = "На переходе нет освещения, вечером пешеходам опасно."
    review = _observation(
        observation_id="ugc-review",
        text=problem,
        url=url,
    )
    geo_context = _observation(
        observation_id="page-geo",
        text=None,
        url=url,
        entity_type="place",
        source_kind="json_ld",
        geo=Point(latitude=56.8510, longitude=53.2010),
        provenance={
            "schema_geo_normalization": {
                "source_declared": True,
                "valid_point": True,
                "geocoding_used": False,
            }
        },
    )
    result = SourceResult(observations=[review, geo_context])

    await _urban_classifier().annotate(
        _urban_request(
            "complaints",
            territory={
                "point": {"latitude": 56.8500, "longitude": 53.2000},
                "radius_meters": 1000,
            },
        ),
        result,
    )

    assert review.quality["intent_evidence"]["complaints"] is True
    spatial = review.provenance["source_page_spatial_context"]
    assert spatial["source_backed"] is True
    assert spatial["basis"] == "same_source_url_unique_declared_geo"
    assert spatial["supporting_observation_ids"] == ["page-geo"]
    assert review.provenance["territory_relevance"]["matched"] is True


@pytest.mark.asyncio
async def test_conflicting_same_page_geo_is_not_used_as_radius_proof():
    url = "https://2gis.ru/izhevsk/firm/example/tab/reviews"
    review = _observation(
        observation_id="ugc-review-conflict",
        text="На переходе нет освещения, вечером пешеходам опасно.",
        url=url,
    )
    geo_a = _observation(
        observation_id="page-geo-a",
        text=None,
        url=url,
        entity_type="place",
        source_kind="json_ld",
        geo=Point(latitude=56.8510, longitude=53.2010),
        provenance={"schema_geo_normalization": {"source_declared": True}},
    )
    geo_b = _observation(
        observation_id="page-geo-b",
        text=None,
        url=url,
        entity_type="place",
        source_kind="json_ld",
        geo=Point(latitude=56.9000, longitude=53.3000),
        provenance={"schema_geo_normalization": {"source_declared": True}},
    )
    result = SourceResult(observations=[review, geo_a, geo_b])

    await _urban_classifier().annotate(
        _urban_request(
            "complaints",
            territory={
                "point": {"latitude": 56.8500, "longitude": 53.2000},
                "radius_meters": 1000,
            },
        ),
        result,
    )

    assert review.quality.get("intent_evidence") in (None, {})
    assert "source_page_spatial_context" not in review.provenance
    assert result.evidence == []
