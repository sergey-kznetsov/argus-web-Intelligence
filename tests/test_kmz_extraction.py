from __future__ import annotations

from io import BytesIO
import zipfile

from argus.extraction.kmz import BoundedKmzExtractor


def make_kmz(entries: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, body in entries.items():
            package.writestr(name, body)
    return buffer.getvalue()


def test_kmz_returns_only_root_doc_kml_and_counts_all_members():
    body = make_kmz(
        {
            "doc.kml": b"<kml><Placemark/></kml>",
            "images/icon.png": b"png",
        }
    )
    result = BoundedKmzExtractor(max_bytes=100_000).extract(body)

    assert result.ok is True
    assert result.kml_body == b"<kml><Placemark/></kml>"
    assert result.member_count == 2
    assert result.declared_uncompressed_bytes > len(result.kml_body or b"")


def test_kmz_requires_root_doc_kml():
    body = make_kmz({"maps/data.kml": b"<kml/>"})
    result = BoundedKmzExtractor(max_bytes=100_000).extract(body)

    assert result.kml_body is None
    assert result.error_code == "KMZ_DOC_KML_MISSING"


def test_kmz_rejects_path_traversal_members_even_when_doc_kml_is_safe():
    body = make_kmz(
        {
            "doc.kml": b"<kml/>",
            "../escape.txt": b"bad",
        }
    )
    result = BoundedKmzExtractor(max_bytes=100_000).extract(body)

    assert result.kml_body is None
    assert result.error_code == "KMZ_MEMBER_PATH_INVALID"


def test_kmz_rejects_case_colliding_doc_member():
    body = make_kmz(
        {
            "doc.kml": b"<kml/>",
            "DOC.KML": b"<kml/>",
        }
    )
    result = BoundedKmzExtractor(max_bytes=100_000).extract(body)

    assert result.kml_body is None
    assert result.error_code == "KMZ_DUPLICATE_MEMBER"


def test_kmz_rejects_declared_uncompressed_budget_overflow():
    body = make_kmz(
        {
            "doc.kml": b"<kml/>" + b"x" * 1_000,
            "resource.bin": b"y" * 1_000,
        }
    )
    result = BoundedKmzExtractor(
        max_bytes=100_000,
        max_uncompressed_bytes=1_500,
        max_member_bytes=2_000,
        max_kml_bytes=2_000,
    ).extract(body)

    assert result.kml_body is None
    assert result.error_code == "KMZ_UNCOMPRESSED_LIMIT_EXCEEDED"


def test_kmz_rejects_doc_kml_runtime_read_over_limit():
    body = make_kmz({"doc.kml": b"x" * 2_000})
    result = BoundedKmzExtractor(
        max_bytes=100_000,
        max_uncompressed_bytes=10_000,
        max_member_bytes=10_000,
        max_kml_bytes=1_000,
    ).extract(body)

    assert result.kml_body is None
    assert result.error_code == "KMZ_KML_TOO_LARGE"


def test_non_zip_is_reported_as_invalid_package():
    result = BoundedKmzExtractor().extract(b"not a zip")

    assert result.kml_body is None
    assert result.error_code == "KMZ_PACKAGE_INVALID"
