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
