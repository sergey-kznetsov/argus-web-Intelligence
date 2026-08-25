from __future__ import annotations

from argus.capabilities import OPERATIONAL_AGENT_BACKENDS
from argus.config import Settings
from argus.crawler.agent.base import AgentBackend
from argus.crawler.agent.browser_use import BrowserUseAgent
from argus.crawler.browser.runtime import BrowserCrawlerRuntime
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.extraction.ooxml import BoundedOoxmlExtractor
from argus.extraction.pdf import BoundedPdfExtractor
from argus.extraction.structured_data import BoundedStructuredDataExtractor
from argus.geocoding.contracts import GeocodeProvider
from argus.geocoding.nominatim import NominatimGeocoder
from argus.history.snapshots import SnapshotService
from argus.history.wayback import WaybackCDXProvider
from argus.maps.overpass import OverpassMapProvider
from argus.maps.registry import MapProviderRegistry
from argus.observability import OperationalMetrics
from argus.orchestrator.adaptive_atomic import AdaptiveResearchAtomicCollectionOrchestrator
from argus.recipes.service import RecipeManager
from argus.research.browser_serp import DuckDuckGoBrowserDiscoveryProvider
from argus.research.coverage import EvidenceAwareHeuristicFollowupResearchPlanner
from argus.research.discovery import DiscoveryService
from argus.research.entities import AreaEntityResearchPlanner
from argus.research.followup import OllamaFollowupResearchPlanner
from argus.research.historical import HistoricalBranchPlanner
from argus.research.planner import OllamaResearchPlanner
from argus.research.searxng import SearxngDiscoveryProvider
from argus.security.runtime_posture import enforce_runtime_security
from argus.security.urls import UrlGuard
from argus.services import ServiceContainer
from argus.sources.json_feed import JSONFeedAdapter
from argus.sources.overpass_map import OverpassSourceAdapter
from argus.sources.public_map_web import PublicMapProvenanceWebAdapter
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
        names.append("duckduckgo_browser")
    return names


def configured_geocoding_provider_names(settings: Settings) -> list[str]:
    return ["nominatim"] if settings.nominatim_url else []


def configured_archive_provider_names(settings: Settings) -> list[str]:
    return ["wayback_cdx"] if settings.wayback_cdx_url else []


def build_agent(settings: Settings, guard: UrlGuard) -> AgentBackend | None:
    if not settings.agent_enabled:
        return None
    if settings.agent_backend == "browser-use":
        return BrowserUseAgent(settings, guard)
    operational = ", ".join(OPERATIONAL_AGENT_BACKENDS)
    raise RuntimeError(
        f"ARGUS agent backend '{settings.agent_backend}' is not operational; "
        f"available backends: {operational}"
    )


def build_discovery(
    settings: Settings,
    guard: UrlGuard,
    browser: BrowserCrawlerRuntime,
) -> DiscoveryService | None:
    providers = []
    if settings.searxng_url:
        providers.append(SearxngDiscoveryProvider(settings))
    if settings.browser_serp_enabled:
        providers.append(DuckDuckGoBrowserDiscoveryProvider(settings, browser))
    if not providers:
        return None
    return DiscoveryService(
        providers=providers,
        url_guard=guard,
        max_queries=settings.discovery_max_queries,
        historical_archive_source_id=(
            "wayback_cdx" if settings.wayback_cdx_url else None
        ),
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
    agent = build_agent(settings, guard)
    discovery = build_discovery(settings, guard, browser)
    geocoder = build_geocoder(settings)
    map_registry = build_map_registry(settings)
    structured_extractor = build_structured_data_extractor(settings)
    registry = SourceRegistry(metrics=metrics)
    registry.register(
        PublicMapProvenanceWebAdapter(
            repository=repository,
            fast=fast,
            browser=browser,
            snapshots=snapshots,
            recipes=recipes,
            agent=agent,
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
    if settings.sitemap_discovery_enabled:
        registry.register(SitemapDiscoveryAdapter(settings, fast))
    if settings.overpass_url:
        overpass_provider = map_registry.get("openstreetmap_overpass")
        registry.register(OverpassSourceAdapter(overpass_provider, snapshots, geocoder))
    if settings.wayback_cdx_url:
        registry.register(WaybackSourceAdapter(WaybackCDXProvider(settings), snapshots))
    planner = OllamaResearchPlanner(settings)
    orchestrator = AdaptiveResearchAtomicCollectionOrchestrator(
        repository=repository,
        registry=registry,
        planner=planner,
        max_concurrency=settings.max_concurrency,
        discovery=discovery,
        historical_branch_planner=HistoricalBranchPlanner(),
        area_entity_planner=AreaEntityResearchPlanner(),
        followup_planner=OllamaFollowupResearchPlanner(
            settings,
            fallback=EvidenceAwareHeuristicFollowupResearchPlanner(),
        ),
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
    )
