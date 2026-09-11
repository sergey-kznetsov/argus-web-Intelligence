from __future__ import annotations

from argus.config import Settings
from argus.crawler.agent.base import AgentBackend
from argus.crawler.agent.browser_use import BrowserUseAgent
from argus.crawler.agent.fallback import SequentialAgentBackend
from argus.crawler.agent.ollama_recipe import OllamaRecipeAgent
from argus.crawler.agent.stagehand import StagehandAgent
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
from argus.llm_runtime import LlmConcurrencyGate
from argus.maps.overpass import OverpassMapProvider
from argus.maps.registry import MapProviderRegistry
from argus.observability import OperationalMetrics
from argus.orchestrator.mandatory_coverage import MandatoryCoverageToolPackOrchestrator
from argus.recipes.service import RecipeManager
from argus.research.browser_serp import (
    BingRssDiscoveryProvider,
    DuckDuckGoFastDiscoveryProvider,
    MojeekFastDiscoveryProvider,
)
from argus.research.coverage import EvidenceAwareHeuristicFollowupResearchPlanner
from argus.research.discovery import DiscoveryService
from argus.research.entities import AreaEntityResearchPlanner
from argus.research.entity_hypotheses import OllamaEntityHypothesisExtractor
from argus.research.followup import OllamaFollowupResearchPlanner
from argus.research.historical import HistoricalBranchPlanner
from argus.research.historical_sources import HistoricalSourceResearchPlanner
from argus.research.intent_coverage import IntentCoverageEvaluator
from argus.research.intent_evidence import OllamaIntentEvidenceClassifier
from argus.research.planner import HeuristicResearchPlanner, OllamaResearchPlanner
from argus.research.query_safety import QuerySafeFollowupResearchPlanner, QuerySafeResearchPlanner
from argus.research.radius_scope import (
    RadiusAwareAreaEntityResearchPlanner,
    RadiusAwareFollowupResearchPlanner,
    RadiusAwareResearchPlanner,
)
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
from argus.sources.atomic_content_web import AtomicContentWebAdapter
from argus.sources.json_feed import JSONFeedAdapter
from argus.sources.mingkh_residential import MingkhResidentialAdapter
from argus.sources.overpass_map import OverpassSourceAdapter
from argus.sources.pastvu import PastVuHistoricalAdapter
from argus.sources.registry import SourceRegistry
from argus.sources.rss import RSSAdapter
from argus.sources.sitemap import SitemapDiscoveryAdapter
from argus.sources.wayback import WaybackSourceAdapter
from argus.storage.factory import build_repository


SERVER_DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SERVER_FALLBACK_OVERPASS_URLS = (
    "https://overpass.private.coffee/api/interpreter",
)
SERVER_OVERPASS_TIMEOUT_SECONDS = 15.0
SERVER_OVERPASS_MAX_RETRIES = 1


def configured_discovery_provider_names(settings: Settings) -> list[str]:
    names: list[str] = []
    if settings.searxng_url:
        names.append("searxng")
    if settings.browser_serp_enabled:
        names.extend(("duckduckgo_fast", "mojeek_fast", "bing_rss"))
    return names


def configured_geocoding_provider_names(settings: Settings) -> list[str]:
    return ["nominatim"] if settings.nominatim_url else []


def configured_archive_provider_names(settings: Settings) -> list[str]:
    return ["wayback_cdx"] if settings.wayback_cdx_url else []


def build_agent(
    settings: Settings,
    guard: UrlGuard,
    llm_gate: LlmConcurrencyGate,
    llm_health: OllamaRuntimeHealth | None,
) -> AgentBackend | None:
    if not settings.llm_enabled or not settings.agent_enabled:
        return None

    builders = {
        "ollama-recipe": lambda: OllamaRecipeAgent(
            settings,
            guard,
            llm_gate=llm_gate,
            llm_health=llm_health,
        ),
        "stagehand": lambda: StagehandAgent(
            settings,
            guard,
            llm_gate=llm_gate,
            llm_health=llm_health,
        ),
        "browser-use": lambda: BrowserUseAgent(
            settings,
            guard,
            llm_gate=llm_gate,
            llm_health=llm_health,
        ),
    }
    if settings.agent_backend == "auto":
        return SequentialAgentBackend(
            [builders[name]() for name in ("ollama-recipe", "stagehand", "browser-use")]
        )
    if settings.agent_backend == "disabled":
        return None
    return builders[settings.agent_backend]()


def build_discovery(
    settings: Settings,
    guard: UrlGuard,
    fast: FastCrawlerRuntime,
) -> DiscoveryService | None:
    providers = []
    if settings.searxng_url:
        providers.append(SearxngDiscoveryProvider(settings))
    if settings.browser_serp_enabled:
        providers.extend(
            (
                DuckDuckGoFastDiscoveryProvider(settings, fast),
                MojeekFastDiscoveryProvider(settings, fast),
                BingRssDiscoveryProvider(settings),
            )
        )
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


def effective_map_settings(settings: Settings) -> Settings:
    """Enable the free nearby-entity inventory on standalone server roles.

    Geo Analyzer supplies coordinates, so a server-side ARGUS collection can use the
    public Overpass endpoint directly for a bounded point+radius inventory. Embedded
    library users keep the previous opt-in behavior and can still configure another
    Overpass endpoint explicitly.

    Public Overpass mirrors are best-effort infrastructure and can stall under load. The
    auto-enabled standalone profile therefore bounds one endpoint attempt to 15 seconds
    and allows exactly one failover attempt. This keeps the optional area inventory inside
    the orchestrator's source-task timeout instead of letting it consume the whole task
    budget. Explicit operator configuration is preserved unchanged.
    """

    if settings.overpass_url or settings.execution_role == "embedded":
        return settings
    return settings.model_copy(
        update={
            "overpass_url": SERVER_DEFAULT_OVERPASS_URL,
            "overpass_timeout_seconds": min(
                float(settings.overpass_timeout_seconds),
                SERVER_OVERPASS_TIMEOUT_SECONDS,
            ),
            "direct_provider_max_retries": SERVER_OVERPASS_MAX_RETRIES,
        }
    )


def build_map_registry(
    settings: Settings,
    *,
    fallback_endpoints: tuple[str, ...] = (),
) -> MapProviderRegistry:
    registry = MapProviderRegistry()
    if settings.overpass_url:
        registry.register(
            OverpassMapProvider(
                settings,
                fallback_endpoints=fallback_endpoints,
            )
        )
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


def _build_initial_planner(
    settings: Settings,
    historical_source_planner: HistoricalSourceResearchPlanner,
    llm_gate: LlmConcurrencyGate,
    llm_health: OllamaRuntimeHealth | None,
) -> ResearchInputPlanner:
    deterministic_primary = HeuristicResearchPlanner(
        max_queries=settings.discovery_max_queries,
        historical_sources=historical_source_planner,
    )
    primary_delegate = (
        OllamaResearchPlanner(
            settings,
            fallback=deterministic_primary,
            llm_gate=llm_gate,
            llm_health=llm_health,
        )
        if settings.llm_enabled
        else deterministic_primary
    )
    primary = RadiusAwareResearchPlanner(primary_delegate)
    fallback = RadiusAwareResearchPlanner(
        HeuristicResearchPlanner(
            max_queries=settings.discovery_max_queries,
            historical_sources=historical_source_planner,
        )
    )
    residential_primary = CuratedResidentialResearchPlanner(
        primary,
        max_queries=settings.discovery_max_queries,
    )
    residential_fallback = CuratedResidentialResearchPlanner(
        fallback,
        max_queries=settings.discovery_max_queries,
    )
    return ResearchInputPlanner(
        QuerySafeResearchPlanner(
            residential_primary,
            fallback=residential_fallback,
            max_queries=settings.discovery_max_queries,
        )
    )


def _build_followup_planner(
    settings: Settings,
    coverage: IntentCoverageEvaluator,
    llm_gate: LlmConcurrencyGate,
    llm_health: OllamaRuntimeHealth | None,
) -> QuerySafeFollowupResearchPlanner:
    deterministic_primary = EvidenceAwareHeuristicFollowupResearchPlanner(
        coverage=coverage
    )
    primary_delegate = (
        OllamaFollowupResearchPlanner(
            settings,
            fallback=deterministic_primary,
            coverage=coverage,
            llm_gate=llm_gate,
            llm_health=llm_health,
        )
        if settings.llm_enabled
        else deterministic_primary
    )
    primary = RadiusAwareFollowupResearchPlanner(primary_delegate)
    fallback = RadiusAwareFollowupResearchPlanner(
        EvidenceAwareHeuristicFollowupResearchPlanner(coverage=coverage)
    )
    residential_primary = CuratedResidentialFollowupResearchPlanner(
        primary,
        coverage=coverage,
    )
    residential_fallback = CuratedResidentialFollowupResearchPlanner(
        fallback,
        coverage=coverage,
    )
    return QuerySafeFollowupResearchPlanner(
        residential_primary,
        fallback=residential_fallback,
    )


def build_services(settings: Settings) -> ServiceContainer:
    """Build evidence-first acquisition with an optional fail-open LLM control layer."""

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
    llm_gate = LlmConcurrencyGate(settings.llm_max_concurrency)
    llm_health = OllamaRuntimeHealth(settings) if settings.llm_enabled else None
    agent = build_agent(settings, guard, llm_gate, llm_health)
    discovery = build_discovery(settings, guard, fast)
    geocoder = build_geocoder(settings)
    auto_enabled_overpass = (
        settings.overpass_url is None and settings.execution_role in {"api", "worker"}
    )
    map_settings = effective_map_settings(settings)
    map_registry = build_map_registry(
        map_settings,
        fallback_endpoints=(
            SERVER_FALLBACK_OVERPASS_URLS if auto_enabled_overpass else ()
        ),
    )
    structured_extractor = build_structured_data_extractor(settings)
    historical_source_planner = HistoricalSourceResearchPlanner(
        catalog_file=settings.historical_source_catalog_file
    )
    coverage = IntentCoverageEvaluator()
    intent_evidence_classifier = None
    if settings.llm_enabled:
        intent_evidence_classifier = SourceScopedIntentEvidenceClassifier(
            OllamaIntentEvidenceClassifier(
                settings,
                llm_gate=llm_gate,
                llm_health=llm_health,
            ),
            source_scoped_intents=RESIDENTIAL_INTENTS,
            llm_health=llm_health,
        )

    registry = SourceRegistry(metrics=metrics)
    generic_web = AtomicContentWebAdapter(
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
    registry.register(SitemapDiscoveryAdapter(settings, fast))
    if map_settings.overpass_url:
        overpass_provider = map_registry.get("openstreetmap_overpass")
        registry.register(
            OverpassSourceAdapter(
                overpass_provider,
                snapshots,
                geocoder,
                skip_ungeocoded_discovery=auto_enabled_overpass,
            )
        )
    if settings.wayback_cdx_url:
        registry.register(WaybackSourceAdapter(WaybackCDXProvider(settings), snapshots))

    planner = _build_initial_planner(
        settings,
        historical_source_planner,
        llm_gate,
        llm_health,
    )
    followup_planner = _build_followup_planner(
        settings,
        coverage,
        llm_gate,
        llm_health,
    )
    supervisor_fallback = HeuristicResearchSupervisor(
        target_sources_per_intent=2,
        coverage=coverage,
    )
    supervisor = (
        OllamaResearchSupervisor(
            settings,
            fallback=supervisor_fallback,
            coverage=coverage,
            target_sources_per_intent=2,
            llm_gate=llm_gate,
            llm_health=llm_health,
        )
        if settings.llm_enabled
        else supervisor_fallback
    )
    orchestrator = MandatoryCoverageToolPackOrchestrator(
        repository=repository,
        registry=registry,
        planner=planner,
        max_concurrency=settings.max_concurrency,
        discovery=discovery,
        historical_branch_planner=HistoricalBranchPlanner(),
        historical_source_planner=historical_source_planner,
        area_entity_planner=RadiusAwareAreaEntityResearchPlanner(AreaEntityResearchPlanner()),
        followup_planner=followup_planner,
        research_supervisor=supervisor,
        entity_hypothesis_extractor=(
            OllamaEntityHypothesisExtractor(
                settings,
                llm_gate=llm_gate,
                llm_health=llm_health,
            )
            if settings.llm_enabled
            else None
        ),
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
        llm_gate=llm_gate,
        llm_required_on_start=(
            settings.llm_enabled
            and settings.llm_required
            and settings.execution_role in {"embedded", "worker"}
        ),
    )