from __future__ import annotations

from argus.capabilities import OPERATIONAL_AGENT_BACKENDS
from argus.config import Settings
from argus.crawler.agent.base import AgentBackend
from argus.crawler.agent.ollama_recipe import OllamaRecipeAgent
from argus.crawler.browser.runtime import BrowserCrawlerRuntime
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.extraction.ooxml import BoundedOoxmlExtractor
from argus.extraction.pdf import BoundedPdfExtractor
from argus.extraction.structured_data import BoundedStructuredDataExtractor
from argus.geocoding.contracts import GeocodeProvider
from argus.geocoding.nominatim import NominatimGeocoder
from argus.history.snapshots import SnapshotService
from argus.history.wayback import WaybackCDXProvider
from argus.llm_health import OllamaRuntimeHealth
from argus.maps.overpass import OverpassMapProvider
from argus.maps.registry import MapProviderRegistry
from argus.observability import OperationalMetrics
from argus.orchestrator.toolpack_aware import (
    ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator,
)
from argus.recipes.service import RecipeManager
from argus.research.browser_serp import DuckDuckGoFastDiscoveryProvider
from argus.research.coverage import EvidenceAwareHeuristicFollowupResearchPlanner
from argus.research.discovery import DiscoveryService
from argus.research.entities import AreaEntityResearchPlanner
from argus.research.entity_hypotheses import OllamaEntityHypothesisExtractor
from argus.research.followup import OllamaFollowupResearchPlanner
from argus.research.historical import HistoricalBranchPlanner
from argus.research.historical_sources import HistoricalSourceResearchPlanner
from argus.research.intent_coverage import IntentCoverageEvaluator
from argus.research.intent_evidence import OllamaIntentEvidenceClassifier
from argus.research.planner import OllamaResearchPlanner
from argus.research.query_safety import QuerySafeFollowupResearchPlanner, QuerySafeResearchPlanner
from argus.research.residential_sources import (
    RESIDENTIAL_INTENTS,
    CuratedResidentialFollowupResearchPlanner,
    CuratedResidentialResearchPlanner,
)
from argus.research.searxng import SearxngDiscoveryProvider
from argus.research.source_routing import DedicatedSourceRoutingDiscoveryService
from argus.research.source_scoped_intents import SourceScopedIntentEvidenceClassifier
from argus.research.supervisor import HeuristicResearchSupervisor, OllamaResearchSupervisor
from argus.research.task_context import ResearchInputPlanner
from argus.security.runtime_posture import enforce_runtime_security
from argus.security.urls import UrlGuard
from argus.services import ServiceContainer
from argus.sources.intent_evidence_web import IntentEvidenceWebAdapter
from argus.sources.json_feed import JSONFeedAdapter
from argus.sources.mingkh_residential import MingkhResidentialAdapter
from argus.sources.overpass_map import OverpassSourceAdapter
from argus.sources.pastvu import PastVuHistoricalAdapter
from argus.sources.registry import SourceRegistry
from argus.sources.rss import RSSAdapter
from argus.sources.sitemap import SitemapDiscoveryAdapter
from argus.sources.wayback import WaybackSourceAdapter
from argus.storage.factory import build_repository


def configured_discovery_provider_names(settings: Settings) -> list[str]:
    names: list[str] = []
    if settings.searxng_url:
        names.append("searxng")
    if settings.browser_serp_enabled:
        names.append("duckduckgo_fast")
    return names


def configured_geocoding_provider_names(settings: Settings) -> list[str]:
    return ["nominatim"] if settings.nominatim_url else []


def configured_archive_provider_names(settings: Settings) -> list[str]:
    return ["wayback_cdx"] if settings.wayback_cdx_url else []


def build_agent(settings: Settings, guard: UrlGuard) -> AgentBackend | None:
    if not settings.agent_enabled:
        return None
    if settings.agent_backend == "ollama-recipe":
        return OllamaRecipeAgent(settings, guard)
    operational = ", ".join(OPERATIONAL_AGENT_BACKENDS)
    raise RuntimeError(
        f"ARGUS agent backend '{settings.agent_backend}' is not operational; "
        f"available backends: {operational}"
    )


def build_discovery(
    settings: Settings,
    guard: UrlGuard,
    fast: FastCrawlerRuntime,
) -> DiscoveryService | None:
    providers = []
    if settings.searxng_url:
        providers.append(SearxngDiscoveryProvider(settings))
    if settings.browser_serp_enabled:
        providers.append(DuckDuckGoFastDiscoveryProvider(settings, fast))
    if not providers:
        return None
    return DedicatedSourceRoutingDiscoveryService(
        providers=providers,
        url_guard=guard,
        max_queries=settings.discovery_max_queries,
        historical_archive_source_id=(
            "wayback_cdx" if settings.wayback_cdx_url else None
        ),
        domain_source_routes={"dom.mingkh.ru": "mingkh_residential"},
    )


def build_geocoder(settings: Settings) -> GeocodeProvider | None:
    if settings.nominatim_url:
        return NominatimGeocoder(settings)
    return None


def build_map_registry(settings: Settings) -> MapProviderRegistry:
    registry = MapProviderRegistry()
    if settings.overpass_url:
        registry.register(OverpassMapProvider(settings))
    return registry


def build_pdf_extractor(settings: Settings) -> BoundedPdfExtractor:
    return BoundedPdfExtractor(
        max_bytes=settings.pdf_max_bytes,
        max_pages=settings.pdf_max_pages,
        max_text_chars=settings.pdf_max_text_chars,
        timeout_seconds=settings.pdf_extract_timeout_seconds,
        memory_mb=settings.pdf_extract_memory_mb,
    )


def build_structured_data_extractor(settings: Settings) -> BoundedStructuredDataExtractor:
    return BoundedStructuredDataExtractor(
        max_bytes=settings.structured_data_max_bytes,
        max_records=settings.structured_data_max_records,
        max_columns=settings.structured_data_max_columns,
        max_cell_chars=settings.structured_data_max_cell_chars,
        max_json_depth=settings.structured_data_max_json_depth,
        max_json_nodes=settings.structured_data_max_json_nodes,
    )


def build_ooxml_extractor(settings: Settings) -> BoundedOoxmlExtractor:
    max_bytes = settings.structured_data_max_bytes
    return BoundedOoxmlExtractor(
        max_bytes=max_bytes,
        max_members=1000,
        max_uncompressed_bytes=min(max_bytes * 4, 20 * 1024 * 1024),
        max_member_bytes=min(max_bytes * 2, 10 * 1024 * 1024),
        max_xml_nodes=settings.structured_data_max_json_nodes,
        max_xml_depth=settings.structured_data_max_json_depth,
        max_records=settings.structured_data_max_records,
        max_columns=settings.structured_data_max_columns,
        max_cell_chars=settings.structured_data_max_cell_chars,
        max_sheets=min(settings.structured_data_max_columns, 50),
    )


def build_services(settings: Settings) -> ServiceContainer:
    settings.ensure_dirs()
    enforce_runtime_security(settings)
    repository = build_repository(settings)
    guard = UrlGuard.from_strings(
        settings.allow_internal_targets,
        deny_values=settings.deny_outbound_hosts,
        public_ports=settings.outbound_public_ports,
    )
    fast = FastCrawlerRuntime(settings, guard)
    browser = BrowserCrawlerRuntime(settings, guard)
    snapshots = SnapshotService(repository)
    recipes = RecipeManager(repository)
    metrics = OperationalMetrics()
    llm_health = OllamaRuntimeHealth(settings)
    agent = build_agent(settings, guard)
    discovery = build_discovery(settings, guard, fast)
    geocoder = build_geocoder(settings)
    map_registry = build_map_registry(settings)
    structured_extractor = build_structured_data_extractor(settings)
    base_intent_evidence_classifier = OllamaIntentEvidenceClassifier(settings)
    intent_evidence_classifier = SourceScopedIntentEvidenceClassifier(
        base_intent_evidence_classifier,
        source_scoped_intents=RESIDENTIAL_INTENTS,
    )
    historical_source_planner = HistoricalSourceResearchPlanner(
        catalog_file=settings.historical_source_catalog_file
    )
    coverage = IntentCoverageEvaluator()
    registry = SourceRegistry(metrics=metrics)
    generic_web = IntentEvidenceWebAdapter(
        repository=repository,
        fast=fast,
        browser=browser,
        snapshots=snapshots,
        recipes=recipes,
        agent=agent,
        intent_evidence_classifier=intent_evidence_classifier,
        sitemap_discovery_enabled=settings.sitemap_discovery_enabled,
        pdf_extractor=build_pdf_extractor(settings),
        structured_data_extractor=structured_extractor,
        ooxml_extractor=build_ooxml_extractor(settings),
        html_table_max_scan_chars=min(settings.structured_data_max_bytes, 1_000_000),
        html_table_max_rows_per_table=min(settings.structured_data_max_records, 200),
        html_table_max_total_rows=settings.structured_data_max_records,
        html_table_max_columns=settings.structured_data_max_columns,
        html_table_max_cell_chars=settings.structured_data_max_cell_chars,
        microdata_max_scan_chars=min(settings.structured_data_max_bytes, 750_000),
        microdata_max_items=min(settings.structured_data_max_records, 100),
        microdata_max_properties_per_item=min(settings.structured_data_max_columns, 100),
        microdata_max_value_chars=settings.structured_data_max_cell_chars,
        kml_max_placemarks=settings.structured_data_max_records,
    )
    registry.register(generic_web)
    registry.register(MingkhResidentialAdapter(generic_web, snapshots))
    registry.register(
        PastVuHistoricalAdapter(
            fast,
            snapshots,
            geocoder=geocoder,
        )
    )
    registry.register(
        RSSAdapter(
            fast,
            snapshots,
            max_items=min(settings.structured_data_max_records, 100),
            max_xml_nodes=settings.structured_data_max_json_nodes,
            max_xml_depth=settings.structured_data_max_json_depth,
            max_title_chars=min(settings.structured_data_max_cell_chars, 1_000),
            max_entry_text_chars=min(
                settings.structured_data_max_cell_chars * 10,
                100_000,
            ),
            max_identifier_chars=min(settings.structured_data_max_cell_chars, 2_000),
        )
    )
    registry.register(JSONFeedAdapter(fast, snapshots, structured_extractor))
    # Explicit source-owned navigation (for example the mandatory residential source)
    # must remain executable even when opportunistic sitemap crawling is disabled. The
    # setting only controls automatic sitemap expansion initiated by Generic Web.
    registry.register(SitemapDiscoveryAdapter(settings, fast))
    if settings.overpass_url:
        overpass_provider = map_registry.get("openstreetmap_overpass")
        registry.register(OverpassSourceAdapter(overpass_provider, snapshots, geocoder))
    if settings.wayback_cdx_url:
        registry.register(WaybackSourceAdapter(WaybackCDXProvider(settings), snapshots))

    ollama_planner = OllamaResearchPlanner(settings)
    residential_planner = CuratedResidentialResearchPlanner(
        ollama_planner,
        max_queries=settings.discovery_max_queries,
    )
    residential_fallback = CuratedResidentialResearchPlanner(
        ollama_planner.fallback,
        max_queries=settings.discovery_max_queries,
    )
    planner = ResearchInputPlanner(
        QuerySafeResearchPlanner(
            residential_planner,
            fallback=residential_fallback,
            max_queries=settings.discovery_max_queries,
        )
    )
    followup_fallback = EvidenceAwareHeuristicFollowupResearchPlanner(coverage=coverage)
    ollama_followup = OllamaFollowupResearchPlanner(
        settings,
        fallback=followup_fallback,
        coverage=coverage,
    )
    residential_followup = CuratedResidentialFollowupResearchPlanner(
        ollama_followup,
        coverage=coverage,
    )
    residential_followup_fallback = CuratedResidentialFollowupResearchPlanner(
        followup_fallback,
        coverage=coverage,
    )
    followup_planner = QuerySafeFollowupResearchPlanner(
        residential_followup,
        fallback=residential_followup_fallback,
    )
    supervisor_fallback = HeuristicResearchSupervisor(
        target_sources_per_intent=2,
        coverage=coverage,
    )
    orchestrator = ToolPackAwareEvidenceStatusAdaptiveResearchOrchestrator(
        repository=repository,
        registry=registry,
        planner=planner,
        max_concurrency=settings.max_concurrency,
        discovery=discovery,
        historical_branch_planner=HistoricalBranchPlanner(),
        historical_source_planner=historical_source_planner,
        area_entity_planner=AreaEntityResearchPlanner(),
        followup_planner=followup_planner,
        research_supervisor=OllamaResearchSupervisor(
            settings,
            fallback=supervisor_fallback,
            coverage=coverage,
            target_sources_per_intent=2,
        ),
        entity_hypothesis_extractor=OllamaEntityHypothesisExtractor(settings),
        intent_coverage=coverage,
        max_followup_rounds=3,
        auto_execute=settings.execution_role == "embedded",
        metrics=metrics,
    )
    return ServiceContainer(
        repository=repository,
        registry=registry,
        map_registry=map_registry,
        orchestrator=orchestrator,
        fast=fast,
        browser=browser,
        metrics=metrics,
        llm_health=llm_health,
        llm_required_on_start=(
            settings.llm_required and settings.execution_role in {"embedded", "worker"}
        ),
    )
