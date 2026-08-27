from __future__ import annotations

from dataclasses import dataclass

from argus.contracts.models import CollectionRequest, Observation
from argus.research.followup import FollowupPlan, FollowupResearchPlanner
from argus.research.intent_coverage import IntentCoverageEvaluator
from argus.research.planner import ResearchPlan, ResearchPlanner

RESIDENTIAL_INTENTS = frozenset(
    {
        "residential_population",
        "residential_premises_count",
    }
)


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


class MingkhResidentialSourceResearchPlanner:
    """Build deterministic discovery queries for residential building facts.

    These intents have one mandatory factual source. Search engines are used only as
    navigation to a public ``dom.mingkh.ru`` house page; their snippets never become
    Evidence. The planner deliberately does not add alternative residential sources.
    """

    version = "mingkh-residential-sources/1"
    supported_intents = RESIDENTIAL_INTENTS
    source = MINGKH_RESIDENTIAL_SOURCE

    def queries(self, request: CollectionRequest, *, limit: int = 2) -> list[str]:
        if limit <= 0:
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
                f'site:{self.source.domain_scope} "{anchor}" "Количество квартир" "Жилых помещений"'
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
        }

    @staticmethod
    def _territory_text(request: CollectionRequest) -> str:
        city = (request.territory.city or "").strip()
        address = (request.territory.address or "").strip()
        if city and address:
            return address if city.casefold() in address.casefold() else f"{city}, {address}"
        return address or city


class CuratedResidentialResearchPlanner:
    """Keep residential intents on their mandatory source without consumer branching.

    Non-residential intents are delegated to the normal ARGUS planner. If a request
    contains only residential intents, no general web queries are emitted: discovery is
    constrained to ``dom.mingkh.ru``. Mixed requests preserve normal research for the
    other intents while residential facts remain source-specific.
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

        other_intents = [intent for intent in request.intents if intent not in RESIDENTIAL_INTENTS]
        if other_intents:
            delegated_request = request.model_copy(update={"intents": other_intents})
            delegated = await self.delegate.plan(delegated_request)
        else:
            delegated = ResearchPlan()

        source_queries = self.source_planner.queries(
            request,
            limit=min(len(residential), self.max_queries),
        )
        queries = _merge_queries(source_queries, delegated.queries, limit=self.max_queries)
        notes = [
            *delegated.notes,
            (
                "curated_residential_source="
                f"{self.source_planner.source.source_id};"
                f"version={self.source_planner.version};"
                "fallback_sources=false"
            ),
        ]
        return ResearchPlan(
            queries=queries,
            tasks=list(delegated.tasks),
            notes=notes,
        )


class CuratedResidentialFollowupResearchPlanner:
    """Keep adaptive residential gap research on the same mandatory source.

    The delegate never sees the source-scoped residential intents, so an LLM or heuristic
    follow-up planner cannot propose alternative factual sources for them. One independent
    ``dom.mingkh.ru`` fact is sufficient for each of these explicitly single-source intents.
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
                f"gaps={','.join(gaps)};fallback_sources=false"
            )
        return FollowupPlan(queries=queries, notes=notes)


def _merge_queries(primary: list[str], secondary: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in [*primary, *secondary]:
        value = " ".join(str(raw).split()).strip()[:512].rstrip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= max(0, int(limit)):
            break
    return result
