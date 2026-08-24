from pathlib import Path

from argus.bootstrap import build_services
from argus.config import Settings
from argus.orchestrator.atomic import AtomicCollectionOrchestrator
from argus.sources.document_web import DocumentAwareGenericWebAdapter
from argus.sources.json_feed import JSONFeedAdapter
from argus.sources.office_web import OfficeAwareGenericWebAdapter
from argus.sources.semantic_web import SemanticWebAdapter
from argus.storage.atomic_sqlite import AtomicSQLiteRepository


def test_bootstrap_uses_atomic_orchestrator_repository_and_document_web(tmp_path: Path):
    settings = Settings(
        execution_role="embedded",
        storage_backend="sqlite",
        db_path=tmp_path / "argus.sqlite",
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
        searxng_url=None,
        overpass_url=None,
        nominatim_url=None,
        wayback_cdx_url=None,
        structured_data_max_bytes=123_456,
        structured_data_max_records=7,
        structured_data_max_columns=8,
        structured_data_max_cell_chars=321,
        structured_data_max_json_depth=9,
        structured_data_max_json_nodes=77,
    )

    services = build_services(settings)

    assert isinstance(services.orchestrator, AtomicCollectionOrchestrator)
    assert isinstance(services.repository, AtomicSQLiteRepository)
    tracked = services.registry.get("generic_web")
    adapter = getattr(tracked, "_adapter", None)
    assert isinstance(adapter, DocumentAwareGenericWebAdapter)
    assert isinstance(adapter, OfficeAwareGenericWebAdapter)
    assert isinstance(adapter, SemanticWebAdapter)

    extractor = adapter.structured_data_extractor
    assert extractor.max_bytes == 123_456
    assert extractor.max_records == 7
    assert extractor.max_columns == 8
    assert extractor.max_cell_chars == 321
    assert extractor.max_json_depth == 9
    assert extractor.max_json_nodes == 77
    assert extractor.max_xml_depth == 9
    assert extractor.max_xml_nodes == 77

    assert adapter.html_table_max_scan_chars == 123_456
    assert adapter.html_table_max_rows_per_table == 7
    assert adapter.html_table_max_total_rows == 7
    assert adapter.html_table_max_columns == 8
    assert adapter.html_table_max_cell_chars == 321

    assert adapter.microdata_max_scan_chars == 123_456
    assert adapter.microdata_max_items == 7
    assert adapter.microdata_max_properties_per_item == 8
    assert adapter.microdata_max_value_chars == 321

    json_feed_tracked = services.registry.get("json_feed")
    json_feed = getattr(json_feed_tracked, "_adapter", None)
    assert isinstance(json_feed, JSONFeedAdapter)
    assert json_feed.structured_extractor is extractor
    assert json_feed.max_items == 7

    ooxml = adapter.ooxml_extractor
    assert ooxml is not None
    assert ooxml.max_bytes == 123_456
    assert ooxml.max_uncompressed_bytes == 493_824
    assert ooxml.max_member_bytes == 246_912
    assert ooxml.max_xml_depth == 9
    assert ooxml.max_xml_nodes == 77
    assert ooxml.max_records == 7
    assert ooxml.max_columns == 8
    assert ooxml.max_cell_chars == 321
    assert ooxml.max_sheets == 8
