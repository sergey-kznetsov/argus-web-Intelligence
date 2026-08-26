import json
from pathlib import Path

import pytest

from argus.config import Settings
from argus.research.historical_catalog import (
    HistoricalSourceCatalog,
    HistoricalSourceCatalogError,
)
from argus.research.historical_sources import (
    RUSSIA_USSR_HISTORICAL_SOURCES,
    HistoricalSourceResearchPlanner,
)


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_operator_catalog_adds_bounded_discovery_candidate(tmp_path: Path):
    path = _write(
        tmp_path / "history.json",
        {
            "sources": [
                {
                    "source_id": "perm_archive",
                    "domain": "archive.permkrai.ru",
                    "kind": "archive_catalogues",
                    "priority": 250,
                    "visual": False,
                    "query_suffix": "история здание",
                }
            ]
        },
    )

    planner = HistoricalSourceResearchPlanner(catalog_file=path)
    by_id = {item.source_id: item for item in planner.sources}

    assert by_id["perm_archive"].origin == "operator_catalog"
    assert by_id["perm_archive"].domain == "archive.permkrai.ru"
    assert planner.catalog_version == "historical-source-catalog/1"
    metadata = {item["source_id"]: item for item in planner.source_metadata()}
    assert metadata["perm_archive"]["catalog_entry_is_evidence"] is False


def test_operator_catalog_cannot_replace_builtin_source(tmp_path: Path):
    path = _write(
        tmp_path / "history.json",
        {
            "sources": [
                {
                    "source_id": "pastvu",
                    "domain": "attacker.example.net",
                    "kind": "historical_context",
                    "priority": 1,
                },
                {
                    "source_id": "pastvu_clone",
                    "domain": "pastvu.com",
                    "kind": "historical_context",
                    "priority": 1,
                },
            ]
        },
    )

    profiles = HistoricalSourceCatalog(RUSSIA_USSR_HISTORICAL_SOURCES).profiles(path)
    by_id = {item.source_id: item for item in profiles}

    assert by_id["pastvu"].domain == "pastvu.com"
    assert by_id["pastvu"].origin == "builtin"
    assert "pastvu_clone" not in by_id


@pytest.mark.parametrize(
    "entry",
    [
        {"source_id": "bad id", "domain": "archive.example.org"},
        {"source_id": "valid", "domain": "https://user:pass@archive.example.org"},
        {"source_id": "valid", "domain": "https://archive.example.org/path"},
        {"source_id": "valid", "domain": "localhost"},
        {"source_id": "valid", "domain": "archive.example.org", "priority": 0},
        {"source_id": "valid", "domain": "archive.example.org", "visual": "yes"},
    ],
)
def test_operator_catalog_rejects_invalid_entries(tmp_path: Path, entry: dict[str, object]):
    path = _write(tmp_path / "invalid.json", {"sources": [entry]})

    with pytest.raises(HistoricalSourceCatalogError):
        HistoricalSourceCatalog(RUSSIA_USSR_HISTORICAL_SOURCES).profiles(path)


def test_catalog_environment_path_is_supported_through_settings(tmp_path: Path, monkeypatch):
    path = _write(
        tmp_path / "env-history.json",
        {
            "sources": [
                {
                    "source_id": "regional_history",
                    "domain": "history.example.org",
                    "kind": "historical_context",
                    "priority": 500,
                }
            ]
        },
    )
    monkeypatch.setenv("ARGUS_HISTORICAL_SOURCE_CATALOG_FILE", str(path))

    settings = Settings()
    planner = HistoricalSourceResearchPlanner(
        catalog_file=settings.historical_source_catalog_file
    )

    assert settings.historical_source_catalog_file == path
    assert any(item.source_id == "regional_history" for item in planner.sources)
