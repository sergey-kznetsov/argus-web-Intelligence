import json
from pathlib import Path

from argus.bootstrap import build_services
from argus.config import Settings
from argus.orchestrator.adaptive_atomic import AdaptiveResearchAtomicCollectionOrchestrator
from argus.research.entities import AreaEntityResearchPlanner
from argus.research.followup import OllamaFollowupResearchPlanner


def test_bootstrap_enables_area_and_adaptive_followup_research(tmp_path: Path):
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
    )

    services = build_services(settings)

    assert isinstance(services.orchestrator, AdaptiveResearchAtomicCollectionOrchestrator)
    assert isinstance(services.orchestrator.area_entity_planner, AreaEntityResearchPlanner)
    assert isinstance(services.orchestrator.followup_planner, OllamaFollowupResearchPlanner)
    assert services.orchestrator.max_followup_rounds == 3


def test_bootstrap_injects_configured_historical_source_catalog(tmp_path: Path):
    catalog = tmp_path / "historical-sources.json"
    catalog.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "regional_archive",
                        "domain": "archive.permkrai.ru",
                        "kind": "archive_catalogues",
                        "priority": 250,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        execution_role="embedded",
        storage_backend="sqlite",
        db_path=tmp_path / "catalog.sqlite",
        token_file=tmp_path / "catalog-token",
        browser_serp_enabled=False,
        historical_source_catalog_file=catalog,
    )

    services = build_services(settings)
    source_ids = {
        item.source_id for item in services.orchestrator.historical_source_planner.sources
    }

    assert "regional_archive" in source_ids
