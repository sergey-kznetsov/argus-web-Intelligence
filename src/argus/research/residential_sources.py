from __future__ import annotations

from dataclasses import dataclass

from argus.contracts.models import CollectionRequest, Observation
from argus.research.followup import FollowupPlan, FollowupResearchPlanner
from argus.research.input_candidates import research_input_candidates
from argus.research.intent_coverage import IntentCoverageEvaluator
from argus.research.planner import ResearchPlan, ResearchPlanner
from argus.sources.base import SourceTask

RESIDENTIAL_INTENTS = frozenset(
    {
        "residential_population",
        "residential_premises_count",
    }
)
JANUS_RESIDENTIAL_TOOL_PACK_ID = "janus.residential_facts"


@dataclass(frozen=True, slots=True)
class ResidentialSourceProfile:
    source_id: str
    domain_scope: str
    access: str = "public_web_browser"
    paid_api: bool = False


MINGKH_RESIDENTIAL_SOURCE = ResidentialSourceProfile(
    source_id="mingkh_residential",
    domain_scope="dom.mingkh.ru",
)


def _is_janus_residential_request(request: CollectionRequest) -> bool:
    return request.tool_pack_id == JANUS_RESIDENTIAL_TOOL_PACK_ID


class MingkhResidentialSourceResearchPlanner:
    """Build deterministic navigation for source-declared residential building facts.

    The Janus tool pack is a strict single-site contour. It enters ``dom.mingkh.ru``
    directly and lets the dedicated adapter operate the site's own address interface in the
    same source task. It never uses search providers, ``site_discovery`` or generic-web source
    tasks. Other residential consumers retain the pre-existing robots/sitemap route unchanged.
    """

    version = "mingkh-residential-sources/7"
    supported_intents = RESIDENTIAL_INTENTS
    source = MINGKH_RESIDENTIAL_SOURCE

    def tasks(self, request: CollectionRequest) -> list[SourceTask]:
        requested = self.supported_intents.intersection(request.intents)
        if not requested:
            return []
        if not self._house_search_text(request):
            return []
        origin = f"https://{self.source.domain_scope}"
        input_candidates = research_input_candidates(request)

        if _is_janus_residential_request(request):
            if requested != {"residential_premises_count"}:
                return []
            return [
                SourceTask(
                    source_id=self.source.source_id,
                    goal="residential_premises_count",
                    url=f"{origin}/",
                    task_key=f"{self.source.source_id}:janus:{request.analysis_id}",
                    metadata={
                        "research_goals": ["residential_premises_count"],
                        "allowed_domains": [self.source.domain_scope],
                        "research_input_candidates": input_candidates,
                        "research_input_candidates_navigation_only": True,
                        "research_input_candidates_are_evidence": False,
                        "research_input_scope": "territory_context",
                        "dedicated_source_direct_entry": True,
                        "dedicated_source_navigation": "address_interface",
                        "source_policy": "janus_single_site_factual_collection",
                        "source_owned_navigation": True,
                        "janus_isolated_contour": True,
                        "external_discovery_allowed": False,
                        "domain_scope": self.source.domain_scope,
                    },
                )
            ]

        return [
            SourceTask(
                source_id="site_discovery",
                goal=sorted(requested)[0],
                url=f"{origin}/robots.txt",
                task_key=f"site_discovery:robots:{origin}",
                metadata={
                    "site_discovery_kind": "robots",
                    "root_host": self.source.domain_scope,
                    "root_origin": origin,
                    "site_discovery_target_source_id": self.source.source_id,
                    "research_goals": sorted(requested),
                    "allowed_domains": [self.source.domain_scope],
                    "research_input_candidates": input_candidates,
                    "research_input_candidates_navigation_only": True,
                    "research_input_candidates_are_evidence": False,
                    "research_input_scope": "territory_context",
                    "dedicated_source_direct_entry": True,
                    "dedicated_source_navigation": "robots_sitemap",
                    "source_policy": "mandatory_single_factual_source",
                    "source_owned_navigation": True,
                },
            )
        ]

    def queries(self, request: CollectionRequest, *, limit: int = 2) -> list[str]:
        """Return fallback navigation queries only for non-Janus residential consumers."""

        if limit <= 0 or _is_janus_residential_request(request):
            return []
        requested = self.supported_intents.intersection(request.intents)
        if not requested:
            return []
        anchor = self._territory_text(request)
        if not anchor:
            return []

        candidates: list[str] = []
        if "residential_premises_count" in requested:
            candidates.append(
                f'site:{self.source.domain_scope} "{anchor}" "Количество квартир"'
            )
        if "residential_population" in requested:
            candidates.append(
                f'site:{self.source.domain_scope} "{anchor}" "Количество жителей"'
            )
        return candidates[:limit]

    def source_metadata(self) -> dict[str, object]:
        return {
            "source_id": self.source.source_id,
            "domain_scope": self.source.domain_scope,
            "access": self.source.access,
            "paid_api": self.source.paid_api,
            "mandatory_for_intents": sorted(self.supported_intents),
            "fallback_sources": False,
            "building_address_required": True,
            "direct_entry": "/robots.txt -> declared sitemap -> same-domain page",
            "janus_direct_entry": "/ -> source-owned address interface -> house page",
            "search_provider_policy": "non_janus_followup_only",
        }

    @staticmethod
    def _house_search_text(request: CollectionRequest) -> str:
        return (request.territory.address or "").strip()

    @staticmethod
    def _territory_text(request: CollectionRequest) -> str:
        city = (request.territory.city or "").strip()
        address = (request.territory.address or "").strip()
        if not address:
            return ""
        if city and city.casefold() not in address.casefold():
            return f"{city}, {address}"
        return address


class CuratedResidentialResearchPlanner:
    """Keep residential intents on their mandatory source without source leakage.

    Janus receives an isolated deterministic single-site route and never delegates to the
    normal ARGUS planner. Non-Janus residential behavior remains unchanged: other intents may
    still be delegated and residential navigation may use the existing source-owned
    robots/sitemap path.
    """

    def __init__(
        self,
        delegate: ResearchPlanner,
        *,
        max_queries: int = 8,
        source_planner: MingkhResidentialSourceResearchPlanner | None = None,
    ) -> None:
        self.delegate = delegate
        self.max_queries = max(1, int(max_queries))
        self.source_planner = source_planner or MingkhResidentialSourceResearchPlanner()

    async def plan(self, request: CollectionRequest) -> ResearchPlan:
        residential = [intent for intent in request.intents if intent in RESIDENTIAL_INTENTS]
        if not residential:
            return await self.delegate.plan(request)

        if _is_janus_residential_request(request):
            source_tasks = self.source_planner.tasks(request)
            return ResearchPlan(
                queries=[],
                tasks=source_tasks,
                notes=[
                    "janus_residential_contour="
                    f"{self.source_planner.source.source_id};"
                    f"version={self.source_planner.version};"
                    f"direct_entry={str(bool(source_tasks)).lower()};"
                    "domain=dom.mingkh.ru;navigation=address_interface;"
                    "generic_discovery=false;search_provider=false;"
                    "analysis=false;fallback_sources=false"
                ],
            )

        other_intents = [intent for intent in request.intents if intent not in RESIDENTIAL_INTENTS]
        if other_intents:
            delegated_request = request.model_copy(update={"intents": other_intents})
            delegated = await self.delegate.plan(delegated_request)
        else:
            delegated = ResearchPlan()

        source_tasks = self.source_planner.tasks(request)
        queries = _merge_queries([], delegated.queries, limit=self.max_queries)
        notes = [
            *delegated.notes,
            (
                "curated_residential_source="
                f"{self.source_planner.source.source_id};"
                f"version={self.source_planner.version};"
                f"direct_entry={str(bool(source_tasks)).lower()};"
                "navigation=robots_sitemap;"
                "search_provider=followup_only;"
                "building_address_required=true;fallback_sources=false"
            ),
        ]
        return ResearchPlan(
            queries=queries,
            tasks=[*source_tasks, *delegated.tasks],
            notes=notes,
        )


class CuratedResidentialFollowupResearchPlanner:
    """Keep adaptive residential gap research on the same mandatory source.

    Janus is deliberately source-owned and has no follow-up search queries. For other
    residential consumers, the pre-existing bounded search-provider navigation fallback is
    retained.
    """

    def __init__(
        self,
        delegate: FollowupResearchPlanner,
        *,
        coverage: IntentCoverageEvaluator | None = None,
        source_planner: MingkhResidentialSourceResearchPlanner | None = None,
    ) -> None:
        self.delegate = delegate
        self.coverage = coverage or IntentCoverageEvaluator()
        self.source_planner = source_planner or MingkhResidentialSourceResearchPlanner()

    async def plan_followups(
        self,
        request: CollectionRequest,
        observations: list[Observation],
        *,
        seen_queries: set[str],
        max_queries: int,
    ) -> FollowupPlan:
        if max_queries <= 0:
            return FollowupPlan()
        residential = [intent for intent in request.intents if intent in RESIDENTIAL_INTENTS]
        if not residential:
            return await self.delegate.plan_followups(
                request,
                observations,
                seen_queries=seen_queries,
                max_queries=max_queries,
            )

        if _is_janus_residential_request(request):
            counts = self.coverage.counts(observations, request=request)
            gaps = [intent for intent in residential if int(counts.get(intent, 0)) < 1]
            notes = [
                "janus_residential_followup="
                f"{self.source_planner.source.source_id};"
                f"gaps={','.join(gaps)};search_provider=false;"
                "external_discovery=false;fallback_sources=false"
            ]
            return FollowupPlan(queries=[], notes=notes)

        other_intents = [intent for intent in request.intents if intent not in RESIDENTIAL_INTENTS]
        if other_intents:
            delegated_request = request.model_copy(update={"intents": other_intents})
            delegated = await self.delegate.plan_followups(
                delegated_request,
                observations,
                seen_queries=seen_queries,
                max_queries=max_queries,
            )
        else:
            delegated = FollowupPlan()

        counts = self.coverage.counts(observations, request=request)
        gaps = [intent for intent in residential if int(counts.get(intent, 0)) < 1]
        source_queries: list[str] = []
        if gaps:
            gap_request = request.model_copy(update={"intents": gaps})
            source_queries = self.source_planner.queries(
                gap_request,
                limit=min(len(gaps), max_queries),
            )
        seen = {" ".join(str(item).split()).strip().casefold() for item in seen_queries}
        source_queries = [
            query for query in source_queries if " ".join(query.split()).casefold() not in seen
        ]
        queries = _merge_queries(source_queries, delegated.queries, limit=max_queries)
        notes = list(delegated.notes)
        if gaps:
            notes.append(
                "residential_followup_source="
                f"{self.source_planner.source.source_id};"
                f"gaps={','.join(gaps)};search_provider=fallback_navigation;"
                "fallback_sources=false"
            )
        return FollowupPlan(queries=queries, notes=notes)


def _merge_queries(primary: list[str], secondary: list[str], *, limit: int) -> list[str]:
    if limit <= 0:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in [*primary, *secondary]:
        value = " ".join(str(raw).split()).strip()[:512].rstrip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= int(limit):
            break
    return result
