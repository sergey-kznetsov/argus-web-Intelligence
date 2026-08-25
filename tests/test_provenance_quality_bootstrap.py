from pathlib import Path

from argus.bootstrap import build_services
from argus.config import Settings
from argus.orchestrator.duplicate_atomic import DuplicateAwareAtomicCollectionOrchestrator
from argus.orchestrator.quality_atomic import QualityAwareAtomicCollectionOrchestrator


def test_bootstrap_uses_quality_aware_atomic_orchestrator(tmp_path: Path):
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

    assert isinstance(services.orchestrator, QualityAwareAtomicCollectionOrchestrator)
    assert isinstance(services.orchestrator, DuplicateAwareAtomicCollectionOrchestrator)
    assert services.orchestrator.provenance_quality.provenance_version == "argus-provenance/1"
    assert services.orchestrator.provenance_quality.quality_version == "evidence-quality/1"
