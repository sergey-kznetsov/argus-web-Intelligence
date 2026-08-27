from __future__ import annotations

import re
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
from argus.research.residential_sources import RESIDENTIAL_INTENTS
from argus.research.territory_relevance import TerritoryRelevanceEvaluator
from argus.sources.base import SourceResult, SourceTask
from argus.sources.generic_web import GenericWebAdapter


class MingkhResidentialAdapter:
    """Collect source-declared residential building facts from ``dom.mingkh.ru``.

    Population is never estimated from apartments, area or other proxies. A fact is
    emitted only when the fetched public page explicitly labels the value. Access
    challenges are detected and reported as blocked; this adapter never solves or bypasses
    CAPTCHA/access-control mechanisms.
    """

    source_id = "mingkh_residential"
    intents = set(RESIDENTIAL_INTENTS)
    domain = "dom.mingkh.ru"
    extractor_version = "mingkh-residential/1"

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
        web: GenericWebAdapter,
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
        if not relevance.matched:
            return SourceResult(
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
                discovered_tasks=self._same_domain_house_tasks(task, fetched, request),
            )

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
                    discovered_tasks=self._same_domain_house_tasks(task, fetched, request),
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
            )
            observations.append(observation)
            evidence_items.append(evidence)

        return SourceResult(
            observations=observations,
            evidence=evidence_items,
            discovered_tasks=self._same_domain_house_tasks(task, fetched, request),
            partial=False,
        )

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
        provenance = {
            "snapshot_id": snapshot_id,
            "extractor": self.extractor_version,
            "source_label": label,
            "territory_relevance": {
                "version": self.territory_relevance.version,
                "basis": relevance_basis,
            },
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
