from argus.contracts.models import CollectionRequest, Observation
from argus.research.territory_relevance import TerritoryRelevanceEvaluator


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="transliterated-map-test",
        analysis_id="transliterated-map-analysis",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["reviews"],
    )


def observation(text: str) -> Observation:
    return Observation(
        observation_id="transliterated-map-observation",
        collection_id="transliterated-map-collection",
        analysis_id="transliterated-map-analysis",
        consumer="transliterated-map-test",
        source="generic_web",
        source_kind="web_page",
        url="https://yandex.com/maps/org/example/123/reviews/",
        entity_type="document",
        text=text,
        content_hash="b" * 64,
    )


def test_yandex_english_address_matches_russian_request_by_street_and_house():
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(),
        observation(
            "Permanently closed: coworking, Perm, Komsomolsky Avenue, 27 — Yandex Maps. "
            "42 reviews."
        ),
    )

    assert result.matched is True
    assert result.basis == "street_and_house_number_transliterated"
    assert result.matched_anchors == ("komsomolsky", "27")


def test_same_house_number_on_other_transliterated_street_is_rejected():
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(),
        observation("Perm, Leninsky Avenue, 27. Public place card."),
    )

    assert result.matched is False
    assert result.basis == "address_anchor_missing"


def test_distant_house_number_does_not_match_transliterated_street():
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(),
        observation(
            "Komsomolsky Avenue history and development. This page contains general context "
            "about the street and district. On July 27 another regional event happened."
        ),
    )

    assert result.matched is False
    assert result.basis == "address_anchor_missing"
