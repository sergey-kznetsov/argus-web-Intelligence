from argus.normalization.public_map_provenance import preferred_public_map_review_url


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


def test_existing_two_gis_review_view_is_not_reopened():
    assert preferred_public_map_review_url(
        "https://2gis.ru/moscow/firm/70000001044357973/tab/reviews"
    ) is None


def test_google_place_url_is_not_guessed_into_review_uri():
    assert preferred_public_map_review_url(
        "https://www.google.com/maps/place/example/data=!4m2!3m1!1s0x123"
    ) is None


def test_lookalike_and_malformed_card_urls_are_not_rewritten():
    assert preferred_public_map_review_url(
        "https://yandex.ru.evil.test/maps/org/company/123456/reviews/"
    ) is None
    assert preferred_public_map_review_url(
        "https://2gis.ru/moscow/firm/not-a-public-id"
    ) is None
