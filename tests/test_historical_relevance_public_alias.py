from argus.contracts.models import CollectionRequest, Observation
from argus.research.historical_relevance import HistoricalTerritoryRelevanceEvaluator


def test_historical_street_context_uses_public_transliteration_api():
    request = CollectionRequest(
        consumer="historical-relevance-test",
        analysis_id="historical-relevance-test",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["historical_context"],
    )
    observation = Observation(
        collection_id="collection-1",
        analysis_id=request.analysis_id,
        consumer=request.consumer,
        source="generic_web",
        source_kind="web_page",
        url="https://example.org/history",
        entity_type="web_page",
        title="История Комсомольского проспекта",
        text=(
            "История Комсомольского проспекта в Перми. "
            "Проспект получил современный облик в советский период."
        ),
        content_hash="history-test",
    )

    result = HistoricalTerritoryRelevanceEvaluator().evaluate(request, observation)

    assert result.matched is True
    assert result.basis == "historical_street_context"
