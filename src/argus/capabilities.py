from __future__ import annotations

from collections.abc import Iterable

from argus.config import Settings
from argus.contracts.models import PROTOCOL_VERSION

OPERATIONAL_AGENT_BACKENDS = ("ollama-recipe",)
UNAVAILABLE_AGENT_BACKENDS = {
    "browser-use": {
        "status": "unavailable",
        "reason_code": "UPSTREAM_DEPENDENCY_CONFLICT",
        "detail": (
            "browser-use 0.13.x pins pypdf==6.14.2 while ARGUS requires "
            "pypdf>=6.16.1 for the patched PDF security baseline"
        ),
    },
    "stagehand": {
        "status": "unavailable",
        "reason_code": "LOCAL_LLM_INTEGRATION_NOT_VALIDATED",
    },
}

STRUCTURED_EXTRACTORS = (
    "json_ld",
    "page_metadata",
    "microformats2",
    "microdata",
    "html_tables",
    "json_feed",
    "csv",
    "tsv",
    "json",
    "xml",
    "geojson",
    "georss",
    "kml",
    "kmz",
)
DOCUMENT_EXTRACTORS = (
    "pdf",
    "docx",
    "xlsx",
    "compressed_structured_data",
)
VISUAL_EXTRACTORS = ("image_references",)
GEOSPATIAL_EXTRACTORS = ("georss", "geojson", "kml", "kmz")


def runtime_capabilities(
    settings: Settings,
    *,
    discovery_providers: Iterable[str],
    geocoding_providers: Iterable[str],
    archive_providers: Iterable[str],
    map_providers: Iterable[str],
) -> dict[str, object]:
    """Return capabilities configured in the current ARGUS process.

    Ollama planner/supervisor/classifier use deterministic fallbacks when the local
    endpoint is unavailable; their configuration therefore does not claim current LLM
    health. AGENT is advertised as active only when explicitly enabled.
    """
    server_queue = settings.execution_role in {"api", "worker"}
    agent_operational = (
        settings.agent_enabled and settings.agent_backend in OPERATIONAL_AGENT_BACKENDS
    )
    runtimes = ["fast", "browser"]
    if agent_operational:
        runtimes.append("agent")

    configured_agent = settings.agent_backend if settings.agent_enabled else None
    return {
        "protocol_version": PROTOCOL_VERSION,
        "runtimes": runtimes,
        "storage": settings.storage_backend,
        "execution_role": settings.execution_role,
        "api_max_request_bytes": settings.api_max_request_bytes,
        "research_intelligence": {
            "llm_backend": "ollama",
            "llm_url": settings.ollama_url,
            "model": settings.ollama_model,
            "planner": "ollama_with_heuristic_fallback",
            "supervisor": "ollama_with_evidence_aware_fallback",
            "semantic_exact_excerpt_classifier": True,
            "custom_consumer_neutral_intents": True,
            "model_output_is_evidence": False,
        },
        "request_contract": {
            "supplemental_source_pool": True,
            "supplemental_source_pool_priority": "normal",
            "seed_urls_are_explicit_targets": True,
            "default_output_language": "ru",
        },
        "result_delivery": {
            "full_result_max_items": settings.api_full_result_max_items,
            "full_result_max_bytes": settings.api_full_result_max_bytes,
            "page_default_size": settings.api_result_page_default_size,
            "page_max_size": settings.api_result_page_max_size,
            "page_max_bytes": settings.api_result_page_max_bytes,
            "pagination": "opaque_keyset",
            "paged_results_require_terminal_status": True,
        },
        "queue_backend": "postgresql_leases" if server_queue else "embedded",
        "idempotent_submission": settings.execution_role == "api",
        "idempotency_window_seconds": (
            settings.idempotency_window_seconds if settings.execution_role == "api" else None
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
                "worker_registration_days": settings.retention_worker_registration_days,
                "maintenance_interval_seconds": settings.retention_maintenance_interval_seconds,
                "batch_size": settings.retention_batch_size,
                "preserve_latest_snapshot_per_url": True,
            }
            if server_queue
            else None
        ),
        "operations": (
            {
                "queue_metrics": True,
                "runtime_metrics": True,
                "collection_listing": True,
                "collection_page_max_size": 100,
                "pagination": "keyset",
            }
            if settings.execution_role == "api"
            else {"runtime_metrics": True}
        ),
        "history": True,
        "historical_timeline": True,
        "historical_images": True,
        "site_recipes": True,
        "sitemap_discovery": settings.sitemap_discovery_enabled,
        "structured_extractors": list(STRUCTURED_EXTRACTORS),
        "document_extractors": list(DOCUMENT_EXTRACTORS),
        "visual_extractors": list(VISUAL_EXTRACTORS),
        "geospatial_extractors": list(GEOSPATIAL_EXTRACTORS),
        "discovery_providers": list(discovery_providers),
        "geocoding_providers": list(geocoding_providers),
        "archive_providers": list(archive_providers),
        "map_providers": list(map_providers),
        "agent_enabled": agent_operational,
        "agent_backend": configured_agent if agent_operational else None,
        "agent_backends": list(OPERATIONAL_AGENT_BACKENDS),
        "unavailable_agent_backends": dict(UNAVAILABLE_AGENT_BACKENDS),
    }
