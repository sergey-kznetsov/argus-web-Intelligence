from argus.contracts.models import CollectionRequest, Observation
from argus.research.territory_relevance import TerritoryRelevanceEvaluator


def request(**territory) -> CollectionRequest:
    return CollectionRequest(
        consumer="test",
        analysis_id="territory-relevance",
        territory=territory,
        intents=["public_mentions"],
    )


def observation(*, text: str = "", geo=None) -> Observation:
    return Observation(
        observation_id="obs-territory",
        collection_id="collection-territory",
        analysis_id="territory-relevance",
        consumer="test",
        source="generic_web",
        source_kind="web_page",
        url="https://example.com/page",
        entity_type="document",
        text=text,
        geo=geo,
        content_hash="c" * 64,
    )


def test_exact_perm_address_is_relevant():
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(city="Пермь", address="Комсомольский проспект, 27"),
        observation(text="Гостиница расположена по адресу Пермь, Комсомольский проспект, 27."),
    )

    assert result.matched is True
    assert result.basis == "exact_address"


def test_same_city_different_address_is_not_relevant_to_address_scope():
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(city="Пермь", address="Комсомольский проспект, 27"),
        observation(text="Пермь, улица Ленина, 58. В здании открылась новая организация."),
    )

    assert result.matched is False
    assert result.basis == "address_anchor_missing"


def test_street_and_house_number_survive_address_format_variation():
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(city="Пермь", address="Комсомольский проспект, д. 27"),
        observation(text="Пермь. Комсомольский пр-т 27 — гостиница Прикамье."),
    )

    assert result.matched is True
    assert result.basis == "street_and_house_number"
    assert "27" in result.matched_anchors


def test_city_only_request_accepts_city_source_context():
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(city="Пермь"),
        observation(text="В Перми завершили реконструкцию общественного пространства."),
    )

    assert result.matched is True
    assert result.basis == "city_phrase"


def test_point_source_geo_inside_radius_is_relevant():
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(
            point={"latitude": 58.010455, "longitude": 56.229443},
            radius_meters=200,
        ),
        observation(
            text="Карточка объекта",
            geo={"latitude": 58.0109, "longitude": 56.2299},
        ),
    )

    assert result.matched is True
    assert result.basis == "source_geo_within_radius"
    assert result.distance_meters is not None
    assert result.distance_meters < 200


def test_point_source_geo_outside_radius_is_rejected():
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(
            point={"latitude": 58.010455, "longitude": 56.229443},
            radius_meters=100,
        ),
        observation(
            text="Карточка объекта",
            geo={"latitude": 58.0200, "longitude": 56.2400},
        ),
    )

    assert result.matched is False
    assert result.basis == "source_geo_outside_radius"
    assert result.distance_meters is not None
    assert result.distance_meters > 100


def test_point_only_without_source_geo_is_not_assumed_relevant():
    evaluator = TerritoryRelevanceEvaluator()
    result = evaluator.evaluate(
        request(point={"latitude": 58.010455, "longitude": 56.229443}),
        observation(text="Публичная страница без координат."),
    )

    assert result.matched is False
    assert result.basis == "source_geo_missing"
