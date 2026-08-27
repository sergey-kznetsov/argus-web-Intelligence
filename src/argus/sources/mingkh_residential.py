from __future__ import annotations

import re
from typing import Protocol
from urllib.parse import urldefrag, urlparse

from bs4 import BeautifulSoup

from argus.contracts.models import (
    CollectionRequest,
    Evidence,
    EvidenceSource,
    Observation,
    StructuredError,
)
from argus.crawler.models import FetchResult
from argus.history.snapshots import SnapshotService, sha256_text
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.research.input_candidates import research_input_candidates
from argus.research.residential_sources import RESIDENTIAL_INTENTS
from argus.research.territory_relevance import TerritoryRelevanceEvaluator
from argus.sources.base import SourceResult, SourceTask


class ResidentialWebRuntime(Protocol):
    """Narrow web-runtime contract required by the dedicated residential source."""

    async def fetch(self, task: SourceTask) -> FetchResult: ...

    async def navigate_with_agent(
        self,
        task: SourceTask,
        *,
        context_fetch: FetchResult,
    ) -> FetchResult | None: ...

    async def finalize_navigation_goal(
        self,
        task: SourceTask,
        request: CollectionRequest,
        result: SourceResult,
    ) -> None: ...

    async def health(self) -> dict[str, object]: ...


class MingkhResidentialAdapter:
    """Collect source-declared residential building facts from ``dom.mingkh.ru``.

    Population is never estimated from apartments, area or other proxies. A fact is
    emitted only when the fetched public page explicitly labels the value. Accessible
    public search/filter interfaces may be traversed through the bounded AGENT -> verified
    SiteRecipe contract. Access challenges are detected and reported as blocked; this
    adapter never solves or bypasses CAPTCHA/access-control mechanisms.
    """

    source_id = "mingkh_residential"
    intents = set(RESIDENTIAL_INTENTS)
    domain = "dom.mingkh.ru"
    extractor_version = "mingkh-residential/2"
    interface_navigation_version = "mingkh-interface-navigation/1"

    _LABELS: dict[str, tuple[str, ...]] = {
        "residential_premises_count": (
            "Количество квартир",
            "Количество жилых помещений",
            "Жилых помещений",
        ),
        "residential_population": (
            "Количество жителей",
            "Численность жителей",
            "Число жителей",
        ),
    }
    _CHALLENGE_MARKERS = (
        "captcha",
        "recaptcha",
        "hcaptcha",
        "verify you are human",
        "robot check",
        "проверка что вы не робот",
        "проверка, что вы не робот",
        "убедиться что вы не робот",
        "убедиться, что вы не робот",
        "решите пример",
    )
    _MAX_VISIBLE_TEXT_CHARS = 150_000
    _MAX_LINKS = 50

    def __init__(
        self,
        web: ResidentialWebRuntime,
        snapshots: SnapshotService,
        *,
        territory_relevance: TerritoryRelevanceEvaluator | None = None,
    ) -> None:
        self.web = web
        self.snapshots = snapshots
        self.territory_relevance = territory_relevance or TerritoryRelevanceEvaluator()

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        requested = RESIDENTIAL_INTENTS.intersection(request.intents)
        if not requested:
            return []
        tasks: list[SourceTask] = []
        input_candidates = research_input_candidates(request)
        for raw in request.constraints.seed_urls:
            url = str(raw)
            if not self._is_domain_url(url):
                continue
            tasks.append(
                SourceTask(
                    source_id=self.source_id,
                    goal=sorted(requested)[0],
                    url=url,
                    metadata={
                        "research_goals": sorted(requested),
                        "allowed_domains": [self.domain],
                        "research_input_candidates": list(input_candidates),
                        "research_input_candidates_navigation_only": True,
                        "research_input_candidates_are_evidence": False,
                        "research_input_scope": "territory_context",
                    },
                )
            )
        return tasks

    async def fetch(self, task: SourceTask) -> FetchResult:
        if not self._is_domain_url(task.url):
            raise ValueError("mingkh residential task must target dom.mingkh.ru")
        return await self.web.fetch(task)

    async def extract(
        self,
        task: SourceTask,
        fetched: FetchResult,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await self._extract_page(
            task,
            fetched,
            request,
            allow_interface_navigation=True,
        )
        await self.web.finalize_navigation_goal(task, request, result)
        return result

    async def _extract_page(
        self,
        task: SourceTask,
        fetched: FetchResult,
        request: CollectionRequest,
        *,
        allow_interface_navigation: bool,
    ) -> SourceResult:
        if not self._is_domain_url(fetched.final_url):
            return SourceResult(
                observations=[],
                partial=True,
                errors=[
                    StructuredError(
                        code="MINGKH_EXTERNAL_REDIRECT",
                        message="dom.mingkh.ru navigation left the configured factual source domain",
                        retryable=False,
                        source_id=self.source_id,
                    )
                ],
            )

        visible_text, chunks = self._visible_text(fetched.text)
        if fetched.blocked or self._has_access_challenge(visible_text):
            return SourceResult(
                observations=[],
                blocked=True,
                partial=False,
                errors=[
                    StructuredError(
                        code="MINGKH_ACCESS_CHALLENGE",
                        message=(
                            "dom.mingkh.ru presented an automated-access challenge; "
                            "ARGUS did not attempt to bypass it"
                        ),
                        retryable=True,
                        source_id=self.source_id,
                    )
                ],
            )

        collection_id = str(task.metadata.get("collection_id", ""))
        snapshot = await self.snapshots.capture(
            self.source_id,
            fetched.final_url,
            fetched.text,
            fetched.content_type,
            collection_id=collection_id,
        )
        page_hash = sha256_text(visible_text)
        page_observation = Observation(
            observation_id=stable_observation_id(
                collection_id=collection_id,
                source_id=self.source_id,
                entity_type="residential_building_page",
                source_url=fetched.final_url,
                content_hash=page_hash,
            ),
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="residential_building_page",
            url=fetched.final_url,
            entity_type="residential_building_page",
            title=fetched.title,
            text=visible_text,
            data={"runtime": fetched.runtime, "status_code": fetched.status_code},
            content_hash=page_hash,
            provenance={"snapshot_id": snapshot.snapshot_id},
            quality={"evidence_backed": False},
        )
        relevance = self.territory_relevance.evaluate(request, page_observation)
        discovered_tasks = self._same_domain_house_tasks(task, fetched, request)
        if not relevance.matched:
            result = SourceResult(
                observations=[],
                partial=True,
                errors=[
                    StructuredError(
                        code="MINGKH_TERRITORY_MISMATCH",
                        message="Fetched dom.mingkh.ru page does not prove the requested building address",
                        retryable=False,
                        source_id=self.source_id,
                    )
                ],
                discovered_tasks=discovered_tasks,
            )
            if self._should_try_interface_navigation(
                task,
                result,
                chunks,
                goal_satisfied=False,
                relevance_matched=False,
                allow_interface_navigation=allow_interface_navigation,
            ):
                return await self._navigate_interface(task, fetched, request, result)
            return result

        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        requested = [intent for intent in request.intents if intent in RESIDENTIAL_INTENTS]
        for intent in requested:
            matches = self._extract_values(chunks, self._LABELS[intent])
            values = {value for _, value in matches}
            if not values:
                continue
            if len(values) > 1:
                return SourceResult(
                    observations=observations,
                    evidence=evidence_items,
                    partial=True,
                    errors=[
                        StructuredError(
                            code="MINGKH_RESIDENTIAL_VALUE_CONFLICT",
                            message=f"dom.mingkh.ru exposes conflicting labeled values for {intent}",
                            retryable=False,
                            source_id=self.source_id,
                        )
                    ],
                    discovered_tasks=discovered_tasks,
                )
            label, value = matches[0]
            observation, evidence = self._fact(
                request=request,
                collection_id=collection_id,
                source_url=fetched.final_url,
                snapshot_id=snapshot.snapshot_id,
                intent=intent,
                label=label,
                value=value,
                relevance_basis=relevance.basis,
                fetch_metadata=fetched.metadata,
            )
            observations.append(observation)
            evidence_items.append(evidence)

        result = SourceResult(
            observations=observations,
            evidence=evidence_items,
            discovered_tasks=discovered_tasks,
            partial=False,
        )
        goal_satisfied = self._goal_satisfied(result, task.goal)
        if self._should_try_interface_navigation(
            task,
            result,
            chunks,
            goal_satisfied=goal_satisfied,
            relevance_matched=True,
            allow_interface_navigation=allow_interface_navigation,
        ):
            return await self._navigate_interface(task, fetched, request, result)
        return result

    async def _navigate_interface(
        self,
        task: SourceTask,
        fetched: FetchResult,
        request: CollectionRequest,
        fallback: SourceResult,
    ) -> SourceResult:
        task.metadata["mingkh_interface_navigation_attempted"] = True
        task.metadata["mingkh_interface_navigation_version"] = self.interface_navigation_version
        self._ensure_research_inputs(task, request)
        guided = await self.web.navigate_with_agent(task, context_fetch=fetched)
        if guided is None:
            task.metadata["mingkh_interface_navigation_result"] = "no_safe_verified_path"
            return fallback

        task.metadata["mingkh_interface_navigation_final_url"] = guided.final_url
        task.metadata["mingkh_interface_navigation_runtime"] = guided.runtime
        guided_result = await self._extract_page(
            task,
            guided,
            request,
            allow_interface_navigation=False,
        )
        if guided_result.blocked:
            task.metadata["mingkh_interface_navigation_result"] = "blocked"
            return guided_result
        if self._goal_satisfied(guided_result, task.goal):
            task.metadata["mingkh_interface_navigation_result"] = "source_goal_revealed"
            return self._merge_successful_guidance(fallback, guided_result)
        if guided_result.observations:
            task.metadata["mingkh_interface_navigation_result"] = "non_goal_source_fact_revealed"
        else:
            task.metadata["mingkh_interface_navigation_result"] = "no_source_fact_revealed"
        return self._merge_unsuccessful_guidance(fallback, guided_result)

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        upstream = await self.web.health()
        return {
            "status": upstream.get("status", "ready"),
            "source_id": self.source_id,
            "domain": self.domain,
            "intents": sorted(self.intents),
            "extractor_version": self.extractor_version,
            "population_estimation": False,
            "access_challenge_policy": "detect_and_report_blocked",
            "interface_navigation": {
                "version": self.interface_navigation_version,
                "enabled": True,
                "mode": "bounded_agent_verified_site_recipe",
                "accessible_pages_only": True,
                "max_rounds_per_task": 1,
                "candidate_requires_source_fact": True,
                "research_input_scope": "territory_context",
                "challenge_bypass": False,
            },
            "upstream_web_runtime": upstream,
        }

    def _fact(
        self,
        *,
        request: CollectionRequest,
        collection_id: str,
        source_url: str,
        snapshot_id: str,
        intent: str,
        label: str,
        value: int,
        relevance_basis: str,
        fetch_metadata: dict[str, object],
    ) -> tuple[Observation, Evidence]:
        evidence_text = f"{label}: {value}"
        content_hash = sha256_text(f"{intent}\x00{value}\x00{label}")
        observation_id = stable_observation_id(
            collection_id=collection_id,
            source_id=self.source_id,
            entity_type="residential_building_fact",
            entity_id=f"{source_url}#{intent}",
            source_url=source_url,
            content_hash=content_hash,
        )
        provenance: dict[str, object] = {
            "snapshot_id": snapshot_id,
            "extractor": self.extractor_version,
            "source_label": label,
            "territory_relevance": {
                "version": self.territory_relevance.version,
                "basis": relevance_basis,
            },
        }
        recipe_id = fetch_metadata.get("recipe_id")
        if isinstance(recipe_id, str) and recipe_id:
            provenance["recipe_id"] = recipe_id
            provenance["recipe_version"] = fetch_metadata.get("recipe_version")
        agent_backend = fetch_metadata.get("agent_backend")
        if isinstance(agent_backend, str) and agent_backend:
            provenance["interface_navigation"] = {
                "version": self.interface_navigation_version,
                "agent_backend": agent_backend,
                "verified_browser_replay": True,
                "agent_output_is_evidence": False,
            }
        observation = Observation(
            observation_id=observation_id,
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="residential_building_fact",
            url=source_url,
            entity_type="residential_building_fact",
            entity_id=f"{source_url}#{intent}",
            title=label,
            text=evidence_text,
            data={
                "intent": intent,
                "value": value,
                "source_label": label,
                "estimated": False,
            },
            content_hash=content_hash,
            provenance=provenance,
            quality={
                "evidence_backed": True,
                "territory_relevant": True,
                "deterministic_label_match": True,
                "estimated": False,
                "intent_evidence": {intent: True},
            },
        )
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation_id,
                evidence_type="residential_building_fact",
                source_url=source_url,
                text=evidence_text,
            ),
            observation_id=observation_id,
            type="residential_building_fact",
            text=evidence_text,
            source=EvidenceSource(
                provider=self.source_id,
                url=source_url,
                collected_at=observation.collected_at,
                source_id=self.source_id,
            ),
            metadata={
                "intent": intent,
                "value": value,
                "source_label": label,
                "estimated": False,
                "provenance": provenance,
            },
        )
        return observation, evidence

    @classmethod
    def _visible_text(cls, html: str) -> tuple[str, list[str]]:
        soup = BeautifulSoup(html or "", "html.parser")
        for element in soup(["script", "style", "noscript", "template"]):
            element.decompose()
        chunks = [" ".join(item.split()) for item in soup.stripped_strings if item.strip()]
        text = "\n".join(chunks)[: cls._MAX_VISIBLE_TEXT_CHARS]
        return text, chunks

    @classmethod
    def _has_access_challenge(cls, text: str) -> bool:
        lowered = " ".join(text.casefold().split())[:50_000]
        return any(marker in lowered for marker in cls._CHALLENGE_MARKERS)

    @classmethod
    def _extract_values(
        cls,
        chunks: list[str],
        labels: tuple[str, ...],
    ) -> list[tuple[str, int]]:
        found: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for index, chunk in enumerate(chunks):
            normalized = " ".join(chunk.split())
            for label in labels:
                match = cls._labeled_number(normalized, label)
                if match is not None:
                    item = (label, match)
                    if item not in seen:
                        seen.add(item)
                        found.append(item)
                    continue
                if label.casefold() not in normalized.casefold():
                    continue
                for next_chunk in chunks[index + 1 : index + 4]:
                    value = cls._standalone_number(next_chunk)
                    if value is None:
                        if len(next_chunk) > 80:
                            break
                        continue
                    item = (label, value)
                    if item not in seen:
                        seen.add(item)
                        found.append(item)
                    break
        return found

    @staticmethod
    def _labeled_number(value: str, label: str) -> int | None:
        match = re.search(
            rf"{re.escape(label)}\s*[:—-]?\s*(\d{{1,9}})(?!\d)",
            value,
            flags=re.IGNORECASE,
        )
        return int(match.group(1)) if match else None

    @staticmethod
    def _standalone_number(value: str) -> int | None:
        match = re.fullmatch(r"\s*(\d{1,9})(?:\s*(?:шт\.?|ед\.?|чел\.?))?\s*", value, re.I)
        return int(match.group(1)) if match else None

    @classmethod
    def _has_explicit_residential_value(cls, chunks: list[str]) -> bool:
        return any(cls._extract_values(chunks, labels) for labels in cls._LABELS.values())

    def _should_try_interface_navigation(
        self,
        task: SourceTask,
        result: SourceResult,
        chunks: list[str],
        *,
        goal_satisfied: bool,
        relevance_matched: bool,
        allow_interface_navigation: bool,
    ) -> bool:
        if not allow_interface_navigation or goal_satisfied:
            return False
        if task.metadata.get("mingkh_interface_navigation_attempted"):
            return False
        if result.blocked or result.discovered_tasks:
            return False
        if not relevance_matched and self._has_explicit_residential_value(chunks):
            # A detail page for another house is not a navigation surface. Never let AGENT
            # turn a proven territory mismatch into evidence for the requested address.
            return False
        return True

    def _ensure_research_inputs(
        self,
        task: SourceTask,
        request: CollectionRequest,
    ) -> None:
        task.metadata["research_input_candidates"] = research_input_candidates(request)
        task.metadata["research_input_candidates_navigation_only"] = True
        task.metadata["research_input_candidates_are_evidence"] = False
        task.metadata["research_input_scope"] = "territory_context"
        task.metadata["allowed_domains"] = [self.domain]

    @staticmethod
    def _goal_satisfied(result: SourceResult, goal: str) -> bool:
        normalized = str(goal or "").strip().casefold()
        if not normalized:
            return False
        return any(
            str(item.data.get("intent") or "").strip().casefold() == normalized
            for item in result.observations
        )

    @classmethod
    def _merge_successful_guidance(
        cls,
        fallback: SourceResult,
        guided: SourceResult,
    ) -> SourceResult:
        """Keep prior valid facts while replacing navigation-surface errors with success."""

        return SourceResult(
            observations=cls._dedupe_observations(
                [*fallback.observations, *guided.observations]
            ),
            evidence=cls._dedupe_evidence([*fallback.evidence, *guided.evidence]),
            discovered_tasks=cls._dedupe_tasks(
                [*fallback.discovered_tasks, *guided.discovered_tasks]
            ),
            blocked=guided.blocked,
            partial=guided.partial,
            errors=list(guided.errors),
        )

    @classmethod
    def _merge_unsuccessful_guidance(
        cls,
        fallback: SourceResult,
        guided: SourceResult,
    ) -> SourceResult:
        """Retain all factual rows and diagnostics when navigation reveals no goal fact."""

        return SourceResult(
            observations=cls._dedupe_observations(
                [*fallback.observations, *guided.observations]
            ),
            evidence=cls._dedupe_evidence([*fallback.evidence, *guided.evidence]),
            discovered_tasks=cls._dedupe_tasks(
                [*fallback.discovered_tasks, *guided.discovered_tasks]
            ),
            blocked=fallback.blocked or guided.blocked,
            partial=fallback.partial or guided.partial,
            errors=cls._dedupe_errors([*fallback.errors, *guided.errors]),
        )

    @staticmethod
    def _dedupe_observations(values: list[Observation]) -> list[Observation]:
        result: list[Observation] = []
        seen: set[str] = set()
        for item in values:
            if item.observation_id in seen:
                continue
            seen.add(item.observation_id)
            result.append(item)
        return result

    @staticmethod
    def _dedupe_evidence(values: list[Evidence]) -> list[Evidence]:
        result: list[Evidence] = []
        seen: set[str] = set()
        for item in values:
            if item.evidence_id in seen:
                continue
            seen.add(item.evidence_id)
            result.append(item)
        return result

    @staticmethod
    def _dedupe_tasks(values: list[SourceTask]) -> list[SourceTask]:
        result: list[SourceTask] = []
        seen: set[str] = set()
        for item in values:
            key = item.dedupe_key
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    @staticmethod
    def _dedupe_errors(values: list[StructuredError]) -> list[StructuredError]:
        result: list[StructuredError] = []
        seen: set[tuple[str, str | None, str]] = set()
        for item in values:
            key = (item.code, item.source_id, item.message)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _same_domain_house_tasks(
        self,
        task: SourceTask,
        fetched: FetchResult,
        request: CollectionRequest,
    ) -> list[SourceTask]:
        if task.depth >= request.constraints.max_depth:
            return []
        result: list[SourceTask] = []
        seen: set[str] = set()
        goals = [intent for intent in request.intents if intent in RESIDENTIAL_INTENTS]
        raw_inputs = task.metadata.get("research_input_candidates", [])
        input_candidates = raw_inputs if isinstance(raw_inputs, list) else []
        for raw in fetched.links:
            url, _ = urldefrag(str(raw))
            if not url or url in seen or not self._is_domain_url(url):
                continue
            path = urlparse(url).path.rstrip("/")
            if re.search(r"/\d{3,}$", path) is None:
                continue
            seen.add(url)
            result.append(
                SourceTask(
                    source_id=self.source_id,
                    goal=task.goal,
                    url=url,
                    depth=task.depth + 1,
                    metadata={
                        "research_goals": goals,
                        "allowed_domains": [self.domain],
                        "research_input_candidates": list(input_candidates),
                        "research_input_candidates_navigation_only": True,
                        "research_input_candidates_are_evidence": False,
                        "research_input_scope": "territory_context",
                        "dedicated_source_followup": True,
                    },
                )
            )
            if len(result) >= self._MAX_LINKS:
                break
        return result

    @classmethod
    def _is_domain_url(cls, url: str) -> bool:
        parsed = urlparse(str(url))
        host = (parsed.hostname or "").casefold().strip(".")
        return parsed.scheme in {"http", "https"} and (
            host == cls.domain or host.endswith(f".{cls.domain}")
        )
