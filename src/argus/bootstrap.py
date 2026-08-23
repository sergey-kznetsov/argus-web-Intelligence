from __future__ import annotations

from argus.config import Settings
from argus.crawler.agent.base import AgentBackend
from argus.crawler.agent.browser_use import BrowserUseAgent
from argus.crawler.agent.stagehand import StagehandAgent
from argus.crawler.browser.runtime import BrowserCrawlerRuntime
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.geocoding.contracts import GeocodeProvider
from argus.geocoding.nominatim import NominatimGeocoder
from argus.history.snapshots import SnapshotService
from argus.history.wayback import WaybackCDXProvider
from argus.maps.overpass import OverpassMapProvider
from argus.maps.registry import MapProviderRegistry
from argus.orchestrator.atomic import AtomicCollectionOrchestrator
from argus.recipes.service import RecipeManager
from argus.research.browser_serp import DuckDuckGoBrowserDiscoveryProvider
from argus.research.discovery import DiscoveryService
from argus.research.historical import HistoricalBranchPlanner
from argus.research.planner import OllamaResearchPlanner
from argus.research.searxng import SearxngDiscoveryProvider
from argus.security.urls import UrlGuard
from argus.services import ServiceContainer
from argus.sources.generic_web import GenericWebAdapter
from argus.sources.overpass_map import OverpassSourceAdapter
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
    if settings.agent_backend == "stagehand":
        return StagehandAgent()
    raise ValueError(f"unsupported ARGUS agent backend: {settings.agent_backend}")


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


def build_services(settings: Settings) -> ServiceContainer:
    settings.ensure_dirs()
    repository = build_repository(settings)
    guard = UrlGuard.from_strings(settings.allow_internal_targets)
    fast = FastCrawlerRuntime(settings, guard)
    browser = BrowserCrawlerRuntime(settings, guard)
    snapshots = SnapshotService(repository)
    recipes = RecipeManager(repository)
    agent = build_agent(settings, guard)
    discovery = build_discovery(settings, guard, browser)
    geocoder = build_geocoder(settings)
    map_registry = build_map_registry(settings)
    registry = SourceRegistry()
    registry.register(
        GenericWebAdapter(
            fast=fast,
            browser=browser,
            snapshots=snapshots,
            recipes=recipes,
            agent=agent,
            sitemap_discovery_enabled=settings.sitemap_discovery_enabled,
        )
    )
    registry.register(RSSAdapter(fast, snapshots))
    if settings.sitemap_discovery_enabled:
        registry.register(SitemapDiscoveryAdapter(settings, fast))
    if settings.overpass_url:
        overpass_provider = map_registry.get("openstreetmap_overpass")
        registry.register(OverpassSourceAdapter(overpass_provider, snapshots, geocoder))
    if settings.wayback_cdx_url:
        registry.register(WaybackSourceAdapter(WaybackCDXProvider(settings), snapshots))
    planner = OllamaResearchPlanner(settings)
    orchestrator = AtomicCollectionOrchestrator(
        repository=repository,
        registry=registry,
        planner=planner,
        max_concurrency=settings.max_concurrency,
        discovery=discovery,
        historical_branch_planner=HistoricalBranchPlanner(),
        auto_execute=settings.execution_role == "embedded",
    )
    return ServiceContainer(
        repository=repository,
        registry=registry,
        map_registry=map_registry,
        orchestrator=orchestrator,
        fast=fast,
        browser=browser,
    )
