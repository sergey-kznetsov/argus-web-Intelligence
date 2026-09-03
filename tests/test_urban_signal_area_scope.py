from __future__ import annotations

import pytest

from argus.consumer_registry import CONSUMER_PROFILE_REGISTRY, ConsumerContractError
from argus.contracts.models import CollectionRequest, Observation, Point, TerritoryContext
from argus.orchestrator.area_atomic import AreaAwareAtomicCollectionOrchestrator
from argus.research.territory_relevance import TerritoryRelevanceEvaluator


def _request(*, consumer: str = "kraken.development.uds") -> CollectionRequest:
    structured = consumer == "kraken.development.uds"
    return CollectionRequest(
        consumer=consumer,
        consumer_profile_version=1 if structured else None,
        capability="urban_signals" if structured else None,
        requested_facts=["complaint", "comment"] if structured else [],
        analysis_id="area-scope-test",
        territory=TerritoryContext(
            city="Ижевск",
            address="Ижевск, улица Пушкинская, 277",
            point=Point(latitude=56.8526, longitude=53.2115),
            metadata={"street": "Пушкинская", "house": "277"},
        ),
        intents=["complaints", "comments", "public_appeals", "incidents"],
    )


def _observation(
    *,
    text: str,
    geo: Point | None = None,
    provenance: dict[str, object] | None = None,
    entity_type: str = "complaint",
) -> Observation:
    return Observation(
        collection_id="collection-1",
        analysis_id="area-scope-test",
        consumer="kraken.development.uds",
        source="fixture",
        source_kind="schema_web",
        url="https://example.test/source",
        entity_type=entity_type,
        text=text,
        geo=geo,
        content_hash="a" * 64,
        provenance=provenance or {},
    )


def test_urban_signals_use_one_kilometre_default_for_source_backed_problem_geo() -> None:
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        _request(),
        _observation(
            text=(
                "Жители сообщают о сломанном тротуаре, опасном переходе и неработающем "
                "освещении рядом с домами."
            ),
            geo=Point(latitude=56.8526, longitude=53.2210),
        ),
    )

    assert result.matched is True
    assert result.basis == "source_geo_within_radius"
    assert result.distance_meters is not None
    assert 250 < result.distance_meters < 1000


def test_urban_signals_accept_source_backed_street_problem_without_house_number() -> None:
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        _request(),
        _observation(
            text=(
                "Ижевск, улица Пушкинская: жители жалуются на состояние тротуара "
                "и освещение вдоль улицы."
            ),
        ),
    )

    assert result.matched is True
    assert result.basis == "urban_signal_street_scope"
    assert "пушкинская" in result.matched_anchors


def test_nearby_establishment_proof_does_not_make_business_review_relevant() -> None:
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        _request(),
        _observation(
            entity_type="review",
            text="Ижевск. Ресторан Пижоны — хороший интерьер и вкусная кухня.",
            provenance={
                "area_entity_branch": {
                    "version": "area-entity-proof/1",
                    "source_backed": True,
                    "entities": [
                        {
                            "source_backed": True,
                            "relevance_basis": "source_geo_within_radius",
                            "distance_meters": 420.0,
                            "anchors": ["Ресторан Пижоны", "Пушкинская улица, 268"],
                        }
                    ],
                }
            },
        ),
    )

    assert result.matched is False
    assert result.basis == "address_anchor_missing"


def test_kraken_street_query_searches_social_problems_not_reviews() -> None:
    queries = AreaAwareAtomicCollectionOrchestrator._street_scope_queries(
        _request(),
        seen_queries=set(),
        limit=1,
    )

    assert len(queries) == 1
    query = queries[0].casefold()
    assert "пушкинская" in query
    assert "жалобы" in query
    assert "проблемы жителей" in query
    assert "отзывы" not in query


def test_kraken_consumer_contract_rejects_establishment_review_fact() -> None:
    with pytest.raises(ConsumerContractError) as error:
        CONSUMER_PROFILE_REGISTRY.resolve(
            consumer="kraken.development.uds",
            capability="urban_signals",
            requested_facts=["review"],
            profile_version=1,
        )

    assert error.value.code == "UNSUPPORTED_REQUESTED_FACT"


def test_generic_policy_does_not_broaden_house_request_to_street_scope() -> None:
    evaluator = TerritoryRelevanceEvaluator()
    generic_request = CollectionRequest(
        consumer="test",
        consumer_profile_version=1,
        capability="generic_research",
        requested_facts=["review"],
        analysis_id="area-scope-test",
        territory=TerritoryContext(
            city="Ижевск",
            address="Ижевск, улица Пушкинская, 277",
            point=Point(latitude=56.8526, longitude=53.2115),
            metadata={"street": "Пушкинская", "house": "277"},
        ),
        intents=["reviews"],
    )
    result = evaluator.evaluate(
        generic_request,
        _observation(text="Ижевск, улица Пушкинская: комментарии жителей."),
    )

    assert result.matched is False
    assert result.basis == "address_anchor_missing"
