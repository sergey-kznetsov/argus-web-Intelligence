from pathlib import Path

from argus.bootstrap import build_services
from argus.config import Settings
from argus.orchestrator.atomic import AtomicCollectionOrchestrator
from argus.sources.document_web import DocumentAwareGenericWebAdapter
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
    extractor = adapter.structured_data_extractor
    assert extractor.max_bytes == 123_456
    assert extractor.max_records == 7
    assert extractor.max_columns == 8
    assert extractor.max_cell_chars == 321
    assert extractor.max_json_depth == 9
    assert extractor.max_json_nodes == 77
