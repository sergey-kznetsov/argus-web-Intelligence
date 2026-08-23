from pathlib import Path

from argus.bootstrap import build_services
from argus.config import Settings
from argus.orchestrator.atomic import AtomicCollectionOrchestrator
from argus.storage.atomic_sqlite import AtomicSQLiteRepository


def test_bootstrap_uses_atomic_orchestrator_and_repository(tmp_path: Path):
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

    assert isinstance(services.orchestrator, AtomicCollectionOrchestrator)
    assert isinstance(services.repository, AtomicSQLiteRepository)
