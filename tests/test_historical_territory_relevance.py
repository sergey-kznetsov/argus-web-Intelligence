from argus.contracts.models import CollectionRequest, Observation
from argus.research.historical_relevance import HistoricalTerritoryRelevanceEvaluator


def request(*, city: str = "Пермь", address: str = "Комсомольский проспект, 27") -> CollectionRequest:
    return CollectionRequest(
        consumer="historical-relevance-test",
        analysis_id="historical-relevance-test",
        territory={"city": city, "address": address},
        intents=["historical_context"],
    )


def observation(text: str) -> Observation:
    return Observation(
        observation_id="historical-relevance-observation",
        collection_id="historical-relevance-collection",
        analysis_id="historical-relevance-test",
        consumer="historical-relevance-test",
        source="generic_web",
        source_kind="web_page",
        url="https://example.org/history",
        entity_type="document",
        text=text,
        content_hash="d" * 64,
    )


def test_containing_street_history_is_relevant_without_house_number():
    evaluator = HistoricalTerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(),
        observation(
            "В Перми история Комсомольского проспекта начинается в XIX веке. "
            "До революции магистраль имела другое название."
        ),
    )

    assert result.matched is True
    assert result.basis == "historical_street_context"
    assert "avenue" in result.matched_anchors


def test_exact_house_history_keeps_strict_relevance_basis():
    evaluator = HistoricalTerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(),
        observation("Пермь, Комсомольский проспект, 27. Дом построен в 1953 году."),
    )

    assert result.matched is True
    assert result.basis == "exact_address"


def test_other_street_history_is_not_relevant_to_requested_address():
    evaluator = HistoricalTerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(),
        observation("В Перми история проспекта Ленина начинается в XIX веке."),
    )

    assert result.matched is False
    assert result.basis == "address_anchor_missing"


def test_same_street_name_in_other_city_is_not_relevant():
    evaluator = HistoricalTerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(),
        observation("В Москве история Комсомольского проспекта начинается в XIX веке."),
    )

    assert result.matched is False
    assert result.basis == "address_anchor_missing"


def test_address_without_street_type_does_not_receive_historical_fallback():
    evaluator = HistoricalTerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(address="Комсомольский, 27"),
        observation("В Перми история Комсомольского проспекта начинается в XIX веке."),
    )

    assert result.matched is False
    assert result.basis == "address_anchor_missing"
