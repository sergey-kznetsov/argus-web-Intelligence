from argus.normalization.public_map_provenance import (
    classify_public_map_url,
    preferred_public_map_review_url,
    public_map_surface_kind,
)


def test_yandex_com_redirect_remains_public_map_entity():
    url = "https://yandex.com/maps/org/zdes_i_seychas/211798575211/reviews/"

    classification = classify_public_map_url(url)

    assert classification is not None
    assert classification["provider"] == "yandex_maps_web"
    assert classification["content_claimed"] is False
    assert public_map_surface_kind(url) == "entity"


def test_yandex_com_entity_gets_same_deterministic_review_view():
    url = "https://yandex.com/maps/org/zdes_i_seychas/211798575211/"

    assert preferred_public_map_review_url(url) == (
        "https://yandex.com/maps/org/zdes_i_seychas/211798575211/reviews/"
    )


def test_yandex_lookalike_domain_is_not_classified():
    assert classify_public_map_url("https://yandex.com.evil.example/maps/org/x/123/") is None
