from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response, status

from argus import __version__
from argus.bootstrap import (
    build_services,
    configured_archive_provider_names,
    configured_discovery_provider_names,
    configured_geocoding_provider_names,
)
from argus.config import Settings, get_settings
from argus.contracts.models import (
    PROTOCOL_VERSION,
    CollectionAccepted,
    CollectionRecord,
    CollectionRequest,
    CollectionResult,
)
from argus.module_protocol import MODULE_ID, runtime_manifest
from argus.observability import configure_logging
from argus.security.auth import bearer_dependency, ensure_token


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    services = build_services(settings)
    repository = services.repository
    registry = services.registry
    orchestrator = services.orchestrator
    require_bearer = bearer_dependency(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ensure_token(settings)
        await services.start()
        app.state.services = services
        app.state.repository = repository
        app.state.registry = registry
        app.state.map_registry = services.map_registry
        app.state.orchestrator = orchestrator
        try:
            yield
        finally:
            await services.shutdown()

    app = FastAPI(title="ARGUS Web Intelligence", version=__version__, lifespan=lifespan)

    async def readiness() -> tuple[bool, dict[str, object]]:
        database = await repository.health()
        ready = database.get("status") == "ok"
        checks: dict[str, object] = {"database": database}
        if settings.execution_role == "api":
            active_workers = 0
            worker_status = "error"
            worker_counter = getattr(repository, "active_worker_count", None)
            if callable(worker_counter):
                try:
                    active_workers = int(
                        await worker_counter(
                            max_age_seconds=settings.worker_health_max_age_seconds
                        )
                    )
                    worker_status = "ok" if active_workers > 0 else "degraded"
                except Exception:
                    worker_status = "error"
            checks["worker"] = {
                "status": worker_status,
                "active_workers": active_workers,
                "max_age_seconds": settings.worker_health_max_age_seconds,
            }
            ready = ready and worker_status == "ok"
        return ready, checks

    @app.get("/v1/manifest", dependencies=[Depends(require_bearer)])
    async def manifest():
        return runtime_manifest()

    @app.get("/v1/health")
    async def health():
        ready, checks = await readiness()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "module_id": MODULE_ID,
            "status": "ok" if ready else "degraded",
            "service": "argus-web-intelligence",
            "version": __version__,
            "execution_role": settings.execution_role,
            "checks": checks,
        }

    @app.get("/v1/capabilities", dependencies=[Depends(require_bearer)])
    async def capabilities():
        return {
            "protocol_version": PROTOCOL_VERSION,
            "runtimes": ["fast", "browser", "agent"],
            "storage": settings.storage_backend,
            "execution_role": settings.execution_role,
            "history": True,
            "site_recipes": True,
            "sitemap_discovery": settings.sitemap_discovery_enabled,
            "structured_extractors": ["json_ld"],
            "discovery_providers": configured_discovery_provider_names(settings),
            "geocoding_providers": configured_geocoding_provider_names(settings),
            "archive_providers": configured_archive_provider_names(settings),
            "map_providers": [provider.provider_id for provider in services.map_registry.all()],
            "agent_enabled": settings.agent_enabled,
            "agent_backend": settings.agent_backend if settings.agent_enabled else None,
            "agent_backends": ["browser-use", "stagehand"],
        }

    @app.post(
        "/v1/collections",
        response_model=CollectionAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_bearer)],
    )
    async def create_collection(request: CollectionRequest):
        return await orchestrator.submit(request)

    @app.get(
        "/v1/collections/{collection_id}",
        response_model=CollectionRecord,
        dependencies=[Depends(require_bearer)],
    )
    async def collection_status(collection_id: str):
        record = await repository.get_collection(collection_id)
        if not record:
            raise HTTPException(status_code=404, detail="collection not found")
        return record

    @app.get(
        "/v1/collections/{collection_id}/result",
        response_model=CollectionResult,
        dependencies=[Depends(require_bearer)],
    )
    async def collection_result(collection_id: str):
        result = await orchestrator.result(collection_id)
        if not result:
            raise HTTPException(status_code=404, detail="collection not found")
        return result

    @app.post(
        "/v1/collections/{collection_id}/cancel",
        response_model=CollectionRecord,
        dependencies=[Depends(require_bearer)],
    )
    async def cancel_collection(collection_id: str):
        record = await orchestrator.cancel(collection_id)
        if not record:
            raise HTTPException(status_code=404, detail="collection not found")
        return record

    @app.get("/v1/sources", dependencies=[Depends(require_bearer)])
    async def sources():
        return [
            {"source_id": source.source_id, "intents": sorted(source.intents)}
            for source in registry.all()
        ]

    @app.get("/v1/sources/{source_id}/health", dependencies=[Depends(require_bearer)])
    async def source_health(source_id: str):
        try:
            return await registry.health(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="source not found") from exc

    @app.head("/v1/health")
    async def health_head():
        ready, _ = await readiness()
        return Response(status_code=200 if ready else 503)

    return app


app = create_app()
