import pytest

from argus.sources.geojson_web import GeoJsonAwareWebAdapter


def test_geojson_point_helper_does_not_shadow_generic_point_dispatch():
    assert "_point" not in GeoJsonAwareWebAdapter.__dict__
    assert "_geojson_point" in GeoJsonAwareWebAdapter.__dict__

    point = GeoJsonAwareWebAdapter._geojson_point([53.2045, 56.8526])

    assert point is not None
    assert point.longitude == pytest.approx(53.2045)
    assert point.latitude == pytest.approx(56.8526)
