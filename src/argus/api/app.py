from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status

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
    CollectionListPage,
    CollectionRecord,
    CollectionRequest,
    CollectionResult,
    CollectionStatus,
    utcnow,
)
from argus.idempotency import request_fingerprint, storage_idempotency_key
from argus.module_protocol import MODULE_ID, runtime_manifest
from argus.observability import configure_logging
from argus.pagination import (
    InvalidCursorError,
    decode_collection_cursor,
    encode_collection_cursor,
)
from argus.security.auth import bearer_dependency, ensure_token
from argus.security.request_limits import RequestSizeLimitMiddleware
from argus.storage.base import IdempotencyConflictError, QueueCapacityError
from argus.storage.postgres_operations import PostgresOperationsStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    services = build_services(settings)
    repository = services.repository
    registry = services.registry
    orchestrator = services.orchestrator
    require_bearer = bearer_dependency(settings)
    operations_store: PostgresOperationsStore | None = None
    if settings.execution_role == "api":
        dsn = settings.database_dsn_value()
        if not dsn:
            raise RuntimeError("server API operations require PostgreSQL DSN")
        operations_store = PostgresOperationsStore(dsn)

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
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.api_max_request_bytes)

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
        server_queue = settings.execution_role in {"api", "worker"}
        return {
            "protocol_version": PROTOCOL_VERSION,
            "runtimes": ["fast", "browser", "agent"],
            "storage": settings.storage_backend,
            "execution_role": settings.execution_role,
            "api_max_request_bytes": settings.api_max_request_bytes,
            "queue_backend": "postgresql_leases" if server_queue else "embedded",
            "idempotent_submission": settings.execution_role == "api",
            "idempotency_window_seconds": (
                settings.idempotency_window_seconds
                if settings.execution_role == "api"
                else None
            ),
            "worker_required_for_readiness": settings.execution_role == "api",
            "queue_limits": (
                {
                    "max_active_collections": settings.queue_max_active_collections,
                    "max_active_per_consumer": settings.queue_max_active_per_consumer,
                    "retry_after_seconds": settings.queue_retry_after_seconds,
                }
                if settings.execution_role == "api"
                else None
            ),
            "retention": (
                {
                    "collection_days": settings.retention_collection_days,
                    "snapshot_days": settings.retention_snapshot_days,
                    "worker_registration_days": (
                        settings.retention_worker_registration_days
                    ),
                    "maintenance_interval_seconds": (
                        settings.retention_maintenance_interval_seconds
                    ),
                    "batch_size": settings.retention_batch_size,
                    "preserve_latest_snapshot_per_url": True,
                }
                if server_queue
                else None
            ),
            "operations": (
                {
                    "queue_metrics": True,
                    "collection_listing": True,
                    "collection_page_max_size": 100,
                    "pagination": "keyset",
                }
                if settings.execution_role == "api"
                else None
            ),
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

    @app.get("/v1/operations/queue", dependencies=[Depends(require_bearer)])
    async def queue_operations():
        metrics_reader = getattr(repository, "queue_metrics", None)
        if settings.execution_role != "api" or not callable(metrics_reader):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="queue operations are available only in server API mode",
            )
        metrics = await metrics_reader(
            worker_max_age_seconds=settings.worker_health_max_age_seconds
        )
        payload = metrics.as_dict()
        payload.update(
            {
                "max_active_collections": settings.queue_max_active_collections,
                "max_active_per_consumer": settings.queue_max_active_per_consumer,
                "idempotency_window_seconds": settings.idempotency_window_seconds,
            }
        )
        return payload

    @app.get(
        "/v1/operations/collections",
        response_model=CollectionListPage,
        dependencies=[Depends(require_bearer)],
    )
    async def collection_operations(
        limit: int = Query(default=50, ge=1, le=100),
        status_filter: CollectionStatus | None = Query(default=None, alias="status"),
        consumer: str | None = Query(default=None, min_length=1, max_length=128),
        cursor: str | None = Query(default=None, max_length=2048),
    ) -> CollectionListPage:
        if operations_store is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="collection operations are available only in server API mode",
            )
        consumer_filter = consumer.strip() if consumer is not None else None
        if consumer is not None and not consumer_filter:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="consumer filter must not be blank",
            )
        decoded_cursor = None
        if cursor is not None:
            try:
                decoded_cursor = decode_collection_cursor(cursor)
            except InvalidCursorError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="invalid collection pagination cursor",
                ) from exc

        items, has_more = await operations_store.list_collections(
            limit=limit,
            status=status_filter,
            consumer=consumer_filter,
            cursor=decoded_cursor,
        )
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = encode_collection_cursor(last.created_at, last.collection_id)
        return CollectionListPage(items=items, next_cursor=next_cursor)

    @app.post(
        "/v1/collections",
        response_model=CollectionAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_bearer)],
    )
    async def create_collection(request: CollectionRequest):
        if settings.execution_role != "api":
            return await orchestrator.submit(request)

        fingerprint = request_fingerprint(request)
        idempotency_key = storage_idempotency_key(request, fingerprint)
        timestamp = utcnow()
        record = CollectionRecord(
            collection_id=str(uuid4()),
            request=request,
            status=CollectionStatus.QUEUED,
            created_at=timestamp,
            updated_at=timestamp,
            stage="queued",
        )
        try:
            stored, _created = await repository.create_collection_idempotent(
                record,
                idempotency_key=idempotency_key,
                request_hash=fingerprint,
                idempotency_window_seconds=settings.idempotency_window_seconds,
                max_active_collections=settings.queue_max_active_collections,
                max_active_per_consumer=settings.queue_max_active_per_consumer,
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="idempotency key is already used by a different collection request",
            ) from exc
        except QueueCapacityError as exc:
            status_code = (
                status.HTTP_429_TOO_MANY_REQUESTS
                if exc.scope == "consumer"
                else status.HTTP_503_SERVICE_UNAVAILABLE
            )
            raise HTTPException(
                status_code=status_code,
                detail={
                    "code": "QUEUE_CAPACITY_REACHED",
                    "scope": exc.scope,
                    "active": exc.current,
                    "limit": exc.limit,
                },
                headers={"Retry-After": str(settings.queue_retry_after_seconds)},
            ) from exc
        return CollectionAccepted(
            collection_id=stored.collection_id,
            status=stored.status,
        )

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
