from argus.normalization.public_map_provenance import (
    preferred_public_map_review_url,
    public_map_surface_kind,
)


def test_yandex_card_resolves_to_public_reviews_view_and_drops_write_review_query():
    assert preferred_public_map_review_url(
        "https://yandex.ru/maps/org/company_name/123456789/reviews/?add-review=true"
    ) is None
    assert preferred_public_map_review_url(
        "https://yandex.ru/maps/org/company_name/123456789/?ll=1%2C2"
    ) == "https://yandex.ru/maps/org/company_name/123456789/reviews/"


def test_two_gis_card_resolves_to_reviews_tab():
    assert preferred_public_map_review_url(
        "https://2gis.ru/moscow/firm/70000001044357973?m=37.6%2C55.7"
    ) == "https://2gis.ru/moscow/firm/70000001044357973/tab/reviews"


def test_two_gis_geo_card_resolves_to_reviews_tab():
    url = "https://2gis.ru/perm/geo/2252435468868331?m=56.2%2C58.0"

    assert public_map_surface_kind(url) == "entity"
    assert preferred_public_map_review_url(url) == (
        "https://2gis.ru/perm/geo/2252435468868331/tab/reviews"
    )


def test_existing_two_gis_review_view_is_not_reopened():
    assert preferred_public_map_review_url(
        "https://2gis.ru/moscow/firm/70000001044357973/tab/reviews"
    ) is None
    assert preferred_public_map_review_url(
        "https://2gis.ru/perm/geo/2252435468868331/tab/reviews"
    ) is None


def test_google_place_url_is_not_guessed_into_review_uri():
    assert preferred_public_map_review_url(
        "https://www.google.com/maps/place/example/data=!4m2!3m1!1s0x123"
    ) is None


def test_map_search_surfaces_are_distinct_from_entity_cards():
    assert public_map_surface_kind(
        "https://2gis.ru/perm/search/%D0%9A%D0%BE%D0%BC%D1%81%D0%BE%D0%BC%D0%BE%D0%BB%D1%8C%D1%81%D0%BA%D0%B8%D0%B9%2027"
    ) == "search"
    assert public_map_surface_kind(
        "https://yandex.ru/maps/?text=%D0%9F%D0%B5%D1%80%D0%BC%D1%8C"
    ) == "search"
    assert public_map_surface_kind(
        "https://www.google.com/maps/search/hotels/@58.0,56.2,12z"
    ) == "search"

    assert public_map_surface_kind(
        "https://2gis.ru/perm/firm/70000001000000000"
    ) == "entity"
    assert public_map_surface_kind(
        "https://2gis.ru/perm/geo/2252435468868331"
    ) == "entity"
    assert public_map_surface_kind(
        "https://yandex.ru/maps/org/prikamye/123456789/reviews/"
    ) == "entity"
    assert public_map_surface_kind(
        "https://www.google.com/maps/place/Prikamye/data=!4m2!3m1!1s0x123"
    ) == "entity"


def test_lookalike_and_malformed_card_urls_are_not_rewritten():
    assert preferred_public_map_review_url(
        "https://yandex.ru.evil.test/maps/org/company/123456/reviews/"
    ) is None
    assert preferred_public_map_review_url(
        "https://2gis.ru/moscow/firm/not-a-public-id"
    ) is None
    assert preferred_public_map_review_url(
        "https://2gis.ru/moscow/geo/not-a-public-id"
    ) is None
    assert public_map_surface_kind("https://2gis.ru.evil.test/perm/search/example") is None
