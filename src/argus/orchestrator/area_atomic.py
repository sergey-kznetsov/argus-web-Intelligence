from __future__ import annotations

from collections.abc import Mapping

from argus.contracts.models import CollectionRequest, Observation
from argus.orchestrator.observed_atomic import ObservedAtomicCollectionOrchestrator
from argus.research.entities import AreaEntityResearchPlanner
from argus.sources.base import SourceTask
from argus.toolpacks import resolved_tool_pack_from_request


class AreaAwareAtomicCollectionOrchestrator(ObservedAtomicCollectionOrchestrator):
    """Atomic orchestrator that recursively researches factual entities found in the area."""

    execution_budget_version = "execution-budget/1"
    area_entity_proof_version = "area-entity-proof/1"

    def __init__(
        self,
        *args,
        area_entity_planner: AreaEntityResearchPlanner | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.area_entity_planner = area_entity_planner

    async def _expand_historical(
        self,
        record,
        task: SourceTask,
        observations: list[Observation],
        pending: list[SourceTask],
        visited: set[str],
        seen_queries: set[str],
    ) -> None:
        self._attach_area_branch_proof(task, observations)
        await self._expand_area_entities(record, task, observations, pending, visited)
        await super()._expand_historical(
            record,
            task,
            observations,
            pending,
            visited,
            seen_queries,
        )

    def _remaining_execution_budget(self, record, visited: set[str]) -> int:
        """Return executable page slots after the current task.

        ``pending`` is intentionally excluded. A queued URL is only a candidate and has not
        consumed the page budget until the orchestrator actually processes it. Counting queue
        length here allowed broad depth-crawl discovery to starve focused research branches.
        """

        return max(
            0,
            int(record.request.constraints.max_pages) - len(visited) - 1,
        )

    async def _expand_area_entities(
        self,
        record,
        task: SourceTask,
        observations: list[Observation],
        pending: list[SourceTask],
        visited: set[str],
    ) -> None:
        if self.discovery is None or self.area_entity_planner is None or not observations:
            return

        raw_depth = task.metadata.get("area_branch_depth", 0)
        try:
            branch_depth = max(0, int(raw_depth))
        except (TypeError, ValueError):
            branch_depth = 0
        if branch_depth >= record.request.constraints.max_depth:
            return

        seen_queries = set(record.checkpoint.get("area_entity_queries", []))
        remaining_page_budget = self._remaining_execution_budget(record, visited)
        if remaining_page_budget <= 0:
            return
        query_limit = min(
            max(1, int(getattr(self.discovery, "max_queries", 8))),
            remaining_page_budget,
        )

        verified_observations, entity_proofs = self._verified_area_entities(
            record.request,
            observations,
        )
        street_queries = self._street_scope_queries(
            record.request,
            seen_queries=seen_queries,
            limit=query_limit,
        )
        remaining_query_slots = max(0, query_limit - len(street_queries))
        entity_queries = self.area_entity_planner.expand(
            record.request,
            verified_observations,
            seen_queries={*seen_queries, *street_queries},
            limit=remaining_query_slots,
        )
        queries = [*street_queries, *entity_queries]
        if not queries:
            return

        seen_queries.update(queries)
        requested_intents = [
            intent
            for intent in record.request.intents
            if intent in self.area_entity_planner.area_intents
        ]
        if not requested_intents:
            return
        branch_constraints = record.request.constraints.model_copy(
            update={"max_pages": remaining_page_budget}
        )
        branch_request = record.request.model_copy(
            update={
                "intents": requested_intents,
                "constraints": branch_constraints,
            }
        )
        outcome = await self.discovery.discover(queries, branch_request)
        for error in outcome.errors:
            if error.code != "DISCOVERY_NO_RESULTS":
                record.errors.append(error)

        additions: list[SourceTask] = []
        for branch_task in outcome.tasks[:remaining_page_budget]:
            if branch_task.dedupe_key in visited:
                continue
            branch_task.metadata["area_branch_depth"] = branch_depth + 1
            branch_task.metadata["area_branch_from"] = task.url
            branch_task.metadata["area_entity_queries"] = list(queries)
            if entity_proofs:
                branch_task.metadata["area_entity_proofs"] = entity_proofs
            additions.append(branch_task)
        self._merge_tasks(pending, additions, record.collection_id)
        record.checkpoint = {
            **record.checkpoint,
            "area_entity_queries": sorted(seen_queries),
            "execution_budget_version": self.execution_budget_version,
        }

    def _verified_area_entities(
        self,
        request: CollectionRequest,
        observations: list[Observation],
    ) -> tuple[list[Observation], list[dict[str, object]]]:
        evaluator = getattr(self.territory_relevance, "evaluator", None)
        if evaluator is None:
            return observations, []

        verified: list[Observation] = []
        proofs: list[dict[str, object]] = []
        for observation in observations:
            result = evaluator.evaluate(request, observation)
            if not result.matched:
                continue
            verified.append(observation)
            if result.basis != "source_geo_within_radius":
                continue
            anchors = self._entity_anchors(observation)
            if not anchors:
                continue
            proof: dict[str, object] = {
                "version": self.area_entity_proof_version,
                "source_backed": True,
                "observation_id": observation.observation_id,
                "source": observation.source,
                "source_url": observation.url,
                "relevance_basis": result.basis,
                "anchors": anchors,
            }
            if observation.geo is not None:
                proof["point"] = {
                    "latitude": observation.geo.latitude,
                    "longitude": observation.geo.longitude,
                }
            if result.distance_meters is not None:
                proof["distance_meters"] = result.distance_meters
            proofs.append(proof)
        return verified, proofs[:8]

    def _attach_area_branch_proof(
        self,
        task: SourceTask,
        observations: list[Observation],
    ) -> None:
        raw = task.metadata.get("area_entity_proofs")
        if not isinstance(raw, list):
            return
        proofs = [dict(item) for item in raw if isinstance(item, Mapping)]
        if not proofs:
            return
        for observation in observations:
            observation.provenance["area_entity_branch"] = {
                "version": self.area_entity_proof_version,
                "source_backed": True,
                "entities": proofs,
            }

    @staticmethod
    def _entity_anchors(observation: Observation) -> list[str]:
        values: list[object] = [observation.title]
        values.extend(
            observation.data.get(key)
            for key in ("name", "operator", "brand", "address")
        )
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            if not isinstance(raw, str):
                continue
            value = " ".join(raw.split()).strip(" \t\r\n-–—|,.;:")[:160]
            if len(value) < 3 or not any(char.isalpha() for char in value):
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result[:4]

    @classmethod
    def _street_scope_queries(
        cls,
        request: CollectionRequest,
        *,
        seen_queries: set[str],
        limit: int,
    ) -> list[str]:
        if limit <= 0:
            return []
        pack = resolved_tool_pack_from_request(request)
        if pack is None or pack.planner_policy != "urban_signals":
            return []
        raw_street = request.territory.metadata.get("street")
        if not isinstance(raw_street, str) or not raw_street.strip():
            return []
        city = (request.territory.city or "").strip()
        street = " ".join(raw_street.split()).strip()
        anchor = f"{city}, {street}" if city else street
        russian = any(
            "а" <= char.casefold() <= "я" or char.casefold() == "ё"
            for char in anchor
        )
        terms = (
            "отзывы комментарии жалобы жители обсуждают"
            if russian
            else "reviews comments complaints residents discussion"
        )
        query = f'"{anchor}" {terms}'[:512].rstrip()
        normalized_seen = {" ".join(value.split()).casefold() for value in seen_queries}
        if query.casefold() in normalized_seen:
            return []
        return [query]
