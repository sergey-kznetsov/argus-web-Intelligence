from __future__ import annotations

from collections.abc import Iterable

from argus.config import Settings
from argus.contracts.models import PROTOCOL_VERSION
from argus.research.source_contours import SourceContourResearchPlanner
from argus.research_profiles import research_profile_catalog

OPERATIONAL_AGENT_BACKENDS: tuple[str, ...] = (
    "ollama-recipe",
    "stagehand",
    "browser-use",
)

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
    """Return configured capabilities without claiming current dependency health."""

    server_queue = settings.execution_role in {"api", "worker"}
    contour_planner = SourceContourResearchPlanner()
    agent_operational = (
        settings.llm_enabled
        and settings.agent_enabled
        and settings.agent_backend != "disabled"
    )
    runtimes = ["fast", "browser"]
    if agent_operational:
        runtimes.append("agent")
    return {
        "protocol_version": PROTOCOL_VERSION,
        "runtimes": runtimes,
        "storage": settings.storage_backend,
        "execution_role": settings.execution_role,
        "api_max_request_bytes": settings.api_max_request_bytes,
        "research_intelligence": {
            "backend": "local_llm_with_deterministic_fallback"
            if settings.llm_enabled
            else "deterministic",
            "llm_enabled": settings.llm_enabled,
            "llm_required": settings.llm_required,
            "llm_backend": "ollama" if settings.llm_enabled else None,
            "llm_url": settings.ollama_url if settings.llm_enabled else None,
            "model": settings.ollama_model if settings.llm_enabled else None,
            "planner": "ollama_with_heuristic_fallback"
            if settings.llm_enabled
            else "heuristic_curated_sources",
            "supervisor": "ollama_with_evidence_aware_fallback"
            if settings.llm_enabled
            else "evidence_aware_heuristic",
            "recursive_followups": True,
            "source_contours": True,
            "source_contour_version": contour_planner.version,
            "entity_hypotheses": settings.llm_enabled,
            "semantic_exact_excerpt_classifier": settings.llm_enabled,
            "consumer_domain_interpretation": True,
            "custom_consumer_neutral_intents": True,
            "model_output_is_evidence": False,
            "llm_max_concurrency": settings.llm_max_concurrency,
            "ollama_profile": {
                "num_thread": settings.ollama_num_thread,
                "num_ctx": settings.ollama_num_ctx,
                "num_predict": settings.ollama_num_predict,
                "keep_alive_seconds": settings.ollama_keep_alive_seconds,
            },
        },
        "research_profiles": research_profile_catalog(),
        "source_contours": {
            "version": contour_planner.version,
            "policies": {
                item["profile_id"]: contour_planner.catalog(str(item["profile_id"]))
                for item in research_profile_catalog()
                if item["source_families"]
            },
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
        "agent_backend": settings.agent_backend if agent_operational else None,
        "agent_backends": list(OPERATIONAL_AGENT_BACKENDS),
        "unavailable_agent_backends": {},
        "agent_fallback_order": (
            list(OPERATIONAL_AGENT_BACKENDS)
            if agent_operational and settings.agent_backend == "auto"
            else []
        ),
        "agent_policy": {
            "escalation": ["fast", "browser", "agent"],
            "navigation_only": True,
            "deterministic_recipe_replay_required": True,
            "captcha_login_paywall_bypass": False,
        },
    }
