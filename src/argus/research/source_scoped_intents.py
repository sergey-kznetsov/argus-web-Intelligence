from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from argus.contracts.models import CollectionRequest, Observation, Point
from argus.llm_health import OllamaRuntimeHealth
from argus.normalization.public_map_provenance import public_map_surface_kind
from argus.research.intent_evidence import IntentEvidenceFinding, OllamaIntentEvidenceClassifier
from argus.sources.base import SourceResult


class DeterministicUrbanSignalEvidenceClassifier:
    """Extract conservative social-problem excerpts without requiring a local LLM.

    This classifier is intentionally narrow. It never treats an establishment review as a
    Kraken fact merely because the page is a review surface. It accepts only exact source
    excerpts that combine an urban/public-space context with a problem marker, or a concrete
    incident marker. Territory relevance is still verified by the shared ARGUS evaluator.

    A source page may borrow a coordinate only when the same fetched URL contains exactly one
    source-declared structured geo point. This gives dynamic public-map UGC an evidence-backed
    radius check without using search-query text or navigation metadata as proof.
    """

    version = "deterministic-urban-signal-evidence/1"
    source_page_spatial_context_version = "source-page-spatial-context/1"
    social_intents = frozenset(
        {
            "comments",
            "discussions",
            "complaints",
            "incidents",
            "posts",
            "public_appeals",
            "resident_messages",
            "local_news",
        }
    )
    max_text_chars = 24_000
    max_segments = 160
    max_findings_per_observation = 4
    min_excerpt_chars = 12
    max_excerpt_chars = 1_000

    _URBAN_MARKERS = (
        "перекрест",
        "перекрёст",
        "переход",
        "пешеход",
        "тротуар",
        "дорог",
        "проезж",
        "машин",
        "автомоб",
        "светофор",
        "освещ",
        "фонар",
        "яма",
        "мусор",
        "голол",
        "снег",
        "парков",
        "двор",
        "подъезд",
        "лифт",
        "отоплен",
        "водоснаб",
        "горячая вода",
        "холодная вода",
        "канализац",
        "останов",
        "транспорт",
        "пробк",
        "стройк",
        "благоустр",
        "детская площад",
        "школ",
        "поликлиник",
        "больниц",
        "resident",
        "pedestrian",
        "crosswalk",
        "intersection",
        "sidewalk",
        "road",
        "traffic",
        "streetlight",
        "parking",
        "yard",
        "entrance",
        "elevator",
        "heating",
        "water supply",
        "sewer",
        "bus stop",
        "public transport",
        "waste",
    )
    _PROBLEM_MARKERS = (
        "жалоб",
        "проблем",
        "опасн",
        "небезопас",
        "не работает",
        "неработ",
        "слом",
        "разбит",
        "разруш",
        "нет переход",
        "нет тротуар",
        "нет освещ",
        "темно",
        "шум",
        "гряз",
        "вон",
        "протеч",
        "течет",
        "течёт",
        "затоп",
        "не убира",
        "не чист",
        "не вывоз",
        "меша",
        "невозмож",
        "под машину",
        "бросаться под",
        "complain",
        "problem",
        "danger",
        "unsafe",
        "does not work",
        "doesn't work",
        "broken",
        "damaged",
        "no crosswalk",
        "no sidewalk",
        "no lighting",
        "noise",
        "dirty",
        "leak",
        "flood",
        "not cleaned",
        "not removed",
    )
    _INCIDENT_MARKERS = (
        "происшеств",
        "авари",
        "дтп",
        "пожар",
        "возгоран",
        "взрыв",
        "обруш",
        "затоп",
        "эваку",
        "пострад",
        "сбил",
        "наезд",
        "incident",
        "accident",
        "crash",
        "fire",
        "explosion",
        "collapse",
        "flood",
        "evacuat",
        "injur",
        "hit a pedestrian",
    )
    _PUBLIC_APPEAL_MARKERS = (
        "просим",
        "прошу",
        "обращаемся",
        "обращение",
        "жители просят",
        "примите меры",
        "please fix",
        "residents ask",
        "public appeal",
    )
    _RESIDENT_MARKERS = (
        "жител",
        "сосед",
        "наш дом",
        "наш двор",
        "во дворе",
        "residents",
        "neighbors",
        "our building",
        "our yard",
    )
    _SEGMENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")

    def __init__(self, delegate: OllamaIntentEvidenceClassifier) -> None:
        self.delegate = delegate

    async def annotate(self, request: CollectionRequest, result: SourceResult) -> SourceResult:
        if result.blocked:
            return result
        requested = self._requested_social_intents(request)
        if not requested:
            return result

        page_contexts = self._source_page_spatial_contexts(result.observations)
        for observation in result.observations:
            text = (observation.text or "").strip()
            if not text:
                continue
            if public_map_surface_kind(observation.url) == "search":
                continue

            relevance = self.delegate.territory_relevance.evaluate(request, observation)
            if not relevance.matched:
                context = page_contexts.get(observation.url)
                if context is not None:
                    point, supporting_ids = context
                    contextual = observation.model_copy(deep=True)
                    contextual.geo = point
                    relevance = self.delegate.territory_relevance.evaluate(request, contextual)
                    if relevance.matched:
                        observation.provenance["source_page_spatial_context"] = {
                            "version": self.source_page_spatial_context_version,
                            "source_backed": True,
                            "basis": "same_source_url_unique_declared_geo",
                            "supporting_observation_ids": supporting_ids,
                            "point": {
                                "latitude": point.latitude,
                                "longitude": point.longitude,
                            },
                            "navigation_metadata_used": False,
                        }
            self.delegate._record_territory_relevance(observation, relevance)
            if not relevance.matched:
                continue

            findings = self._findings(
                text,
                requested,
                entity_type=observation.entity_type,
            )
            if not findings:
                continue
            self._apply_findings(
                observation,
                result,
                findings,
                relevance=relevance,
            )
        return result

    def _requested_social_intents(self, request: CollectionRequest) -> list[str]:
        if request.capability != "urban_signals":
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw in request.intents:
            intent = " ".join(str(raw).split()).strip().casefold()
            if intent not in self.social_intents or intent in seen:
                continue
            seen.add(intent)
            result.append(intent)
        return result

    def _source_page_spatial_contexts(
        self,
        observations: list[Observation],
    ) -> dict[str, tuple[Point, list[str]]]:
        by_url: dict[str, dict[tuple[float, float], list[str]]] = defaultdict(dict)
        for observation in observations:
            if observation.geo is None:
                continue
            normalization = observation.provenance.get("schema_geo_normalization")
            if not isinstance(normalization, dict) or normalization.get("source_declared") is not True:
                continue
            key = (observation.geo.latitude, observation.geo.longitude)
            ids = by_url[observation.url].setdefault(key, [])
            ids.append(observation.observation_id)

        result: dict[str, tuple[Point, list[str]]] = {}
        for url, points in by_url.items():
            if len(points) != 1:
                continue
            (latitude, longitude), observation_ids = next(iter(points.items()))
            result[url] = (
                Point(latitude=latitude, longitude=longitude),
                list(dict.fromkeys(observation_ids)),
            )
        return result

    def _findings(
        self,
        text: str,
        requested: list[str],
        *,
        entity_type: str,
    ) -> list[IntentEvidenceFinding]:
        requested_set = set(requested)
        findings: list[IntentEvidenceFinding] = []
        seen: set[tuple[str, str]] = set()
        segments = self._segments(text)
        for excerpt in segments:
            normalized = excerpt.casefold()
            urban_marker = self._first_marker(normalized, self._URBAN_MARKERS)
            problem_marker = self._first_marker(normalized, self._PROBLEM_MARKERS)
            incident_marker = self._first_marker(normalized, self._INCIDENT_MARKERS)
            appeal_marker = self._first_marker(normalized, self._PUBLIC_APPEAL_MARKERS)
            resident_marker = self._first_marker(normalized, self._RESIDENT_MARKERS)

            candidates: list[tuple[str, str]] = []
            if incident_marker is not None and "incidents" in requested_set:
                candidates.append(("incidents", incident_marker))
            if (
                urban_marker is not None
                and problem_marker is not None
                and "complaints" in requested_set
            ):
                candidates.append(("complaints", problem_marker))
            if (
                appeal_marker is not None
                and urban_marker is not None
                and problem_marker is not None
                and "public_appeals" in requested_set
            ):
                candidates.append(("public_appeals", appeal_marker))
            if (
                resident_marker is not None
                and urban_marker is not None
                and problem_marker is not None
                and "resident_messages" in requested_set
            ):
                candidates.append(("resident_messages", resident_marker))

            normalized_entity = entity_type.strip().casefold()
            if candidates and normalized_entity in {"comment", "discussion"}:
                if "comments" in requested_set:
                    candidates.append(("comments", "source_entity:comment"))
                if normalized_entity == "discussion" and "discussions" in requested_set:
                    candidates.append(("discussions", "source_entity:discussion"))
            if candidates and normalized_entity == "post" and "posts" in requested_set:
                candidates.append(("posts", "source_entity:post"))
            if candidates and normalized_entity in {"publication", "article"} and "local_news" in requested_set:
                candidates.append(("local_news", "source_entity:publication"))

            for intent, marker in candidates:
                key = (intent, excerpt)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    IntentEvidenceFinding(
                        intent=intent,
                        excerpt=excerpt,
                        marker=f"deterministic:{marker}",
                    )
                )
                if len(findings) >= self.max_findings_per_observation:
                    return findings
        return findings

    def _segments(self, text: str) -> list[str]:
        bounded = text[: self.max_text_chars]
        result: list[str] = []
        for raw in self._SEGMENT_SPLIT_RE.split(bounded):
            excerpt = raw.strip()
            if not (self.min_excerpt_chars <= len(excerpt) <= self.max_excerpt_chars):
                continue
            result.append(excerpt)
            if len(result) >= self.max_segments:
                break
        return result

    @staticmethod
    def _first_marker(text: str, markers: tuple[str, ...]) -> str | None:
        for marker in markers:
            if marker in text:
                return marker
        return None

    def _apply_findings(
        self,
        observation: Observation,
        result: SourceResult,
        findings: list[IntentEvidenceFinding],
        *,
        relevance,
    ) -> None:
        raw_classifier = observation.provenance.get("intent_evidence_classifier")
        classifier_start = len(raw_classifier) if isinstance(raw_classifier, list) else 0
        evidence_start = len(result.evidence)

        self.delegate._apply_findings(
            observation,
            result.evidence,
            findings,
            relevance=relevance,
            relevance_version=self.delegate.territory_relevance.version,
        )

        classifier_items = observation.provenance.get("intent_evidence_classifier")
        if isinstance(classifier_items, list):
            for item in classifier_items[classifier_start:]:
                if not isinstance(item, dict):
                    continue
                item["version"] = self.version
                item["semantic_label_model_assisted"] = False
                item["deterministic_rule_based"] = True
                item["model_output_is_evidence"] = False

        for evidence in result.evidence[evidence_start:]:
            if evidence.observation_id != observation.observation_id:
                continue
            if evidence.type != "semantic_intent_excerpt":
                continue
            evidence.metadata["classifier_version"] = self.version
            evidence.metadata["semantic_label_model_assisted"] = False
            evidence.metadata["deterministic_rule_based"] = True
            evidence.metadata["model_output_is_evidence"] = False


class SourceScopedIntentEvidenceClassifier:
    """Apply source-scoped gates and a keyless evidence-first urban-signal fallback."""

    version = "source-scoped-intent-evidence/3"

    def __init__(
        self,
        delegate: OllamaIntentEvidenceClassifier,
        *,
        source_scoped_intents: Iterable[str],
        llm_health: OllamaRuntimeHealth | None = None,
    ) -> None:
        self.delegate = delegate
        self.source_scoped_intents = frozenset(
            str(item).strip().casefold()
            for item in source_scoped_intents
            if str(item).strip()
        )
        self.urban_signals = DeterministicUrbanSignalEvidenceClassifier(delegate)
        self.llm_health = llm_health

    async def annotate(self, request: CollectionRequest, result: SourceResult) -> SourceResult:
        generic_intents = [
            intent
            for intent in request.intents
            if str(intent).strip().casefold() not in self.source_scoped_intents
        ]
        if not generic_intents:
            return result
        scoped_request = request.model_copy(update={"intents": generic_intents})

        if request.capability == "urban_signals":
            result = await self.urban_signals.annotate(scoped_request, result)
            if self.llm_health is not None:
                health = await self.llm_health.check()
                if not health.ready:
                    return result

        return await self.delegate.annotate(scoped_request, result)

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)
