from __future__ import annotations

from collections.abc import Iterable

from argus.contracts.models import CollectionRequest, Observation
from argus.research.territory_relevance import TerritoryRelevanceEvaluator
from argus.research.url_identity import canonicalize_discovery_url


class IntentCoverageEvaluator:
    """Determine achieved research coverage from factual observations.

    ``research_goals`` describe why ARGUS visited a source. They are navigation and
    planning metadata, not proof that the source actually contained the requested
    fact. Coverage is therefore credited only from factual/source-declared shapes or
    an explicit ``intent_evidence`` quality marker.

    When request context is supplied, factual intent coverage additionally requires
    source-backed territorial relevance. A semantically correct review, comment or
    article from another address must never close the requested territory's research gap.
    """

    version = "intent-evidence-coverage/6"
    territory_relevance = TerritoryRelevanceEvaluator()
    _PUBLICATION_SCHEMA_TYPES = {
        "Article",
        "NewsArticle",
        "BlogPosting",
        "DiscussionForumPosting",
        "Report",
        "SocialMediaPosting",
    }
    _HISTORICAL_SOURCE_KINDS = {
        "historical_page_version",
        "historical_entity_change",
    }

    def supports(
        self,
        observation: Observation,
        intent: str,
        *,
        request: CollectionRequest | None = None,
    ) -> bool:
        normalized = intent.strip().casefold()
        if not normalized:
            return False

        explicit = observation.quality.get("intent_evidence")
        explicit_match = False
        if isinstance(explicit, dict) and explicit.get(normalized) is True:
            explicit_match = True
        elif isinstance(explicit, list) and normalized in {
            str(item).strip().casefold() for item in explicit
        }:
            explicit_match = True
        if explicit_match:
            return self._territory_supports(observation, normalized, request)

        entity_type = observation.entity_type.casefold().strip()
        source_kind = observation.source_kind.casefold().strip()
        schema_types = set(self._schema_types(observation))

        if normalized == "reviews":
            return entity_type == "review" and self._territory_supports(
                observation, normalized, request
            )
        if normalized == "comments":
            return entity_type == "comment" and self._territory_supports(
                observation, normalized, request
            )
        if normalized == "discussions":
            structural = entity_type == "comment" or "DiscussionForumPosting" in schema_types
            return structural and self._territory_supports(observation, normalized, request)
        if normalized == "local_news":
            structural = (
                "NewsArticle" in schema_types
                or source_kind in {"feed_entry", "json_feed_item"}
            )
            return structural and self._territory_supports(observation, normalized, request)
        if normalized == "public_mentions":
            return self._is_public_mention(observation, schema_types, request=request)
        if normalized == "historical_context":
            structural = (
                source_kind in self._HISTORICAL_SOURCE_KINDS
                or self._has_archive_provenance(observation)
            )
            return structural and self._territory_supports(observation, normalized, request)
        if normalized == "images":
            structural = entity_type == "image" or source_kind == "image_reference"
            return structural and self._territory_supports(observation, normalized, request)
        if normalized == "historical_images":
            image_reference = entity_type == "image" or source_kind == "image_reference"
            if not image_reference:
                return False
            historical = (
                self._has_archive_provenance(observation)
                or self._has_historical_image_provenance(observation)
            )
            return historical and self._territory_supports(observation, normalized, request)

        # Incidents, complaints and consumer-defined intents require semantic relevance,
        # not merely a page fetched for that goal. They remain uncovered until a factual
        # extractor/classifier emits an explicit intent_evidence marker.
        return False

    def counts(
        self,
        observations: Iterable[Observation],
        *,
        request: CollectionRequest | None = None,
    ) -> dict[str, int]:
        urls_by_intent: dict[str, set[str]] = {}
        for observation in observations:
            source_identity = self._source_identity(observation)
            for intent in self._candidate_intents(observation):
                if self.supports(observation, intent, request=request):
                    urls_by_intent.setdefault(intent, set()).add(source_identity)
        return {intent: len(urls) for intent, urls in urls_by_intent.items()}

    @staticmethod
    def _source_identity(observation: Observation) -> str:
        canonical = canonicalize_discovery_url(observation.url)
        return canonical or observation.url or observation.observation_id

    def _candidate_intents(self, observation: Observation) -> set[str]:
        candidates = {
            "reviews",
            "comments",
            "discussions",
            "local_news",
            "public_mentions",
            "historical_context",
            "images",
            "historical_images",
        }
        explicit = observation.quality.get("intent_evidence")
        if isinstance(explicit, dict):
            candidates.update(str(key).strip().casefold() for key in explicit)
        elif isinstance(explicit, list):
            candidates.update(str(item).strip().casefold() for item in explicit)
        return {item for item in candidates if item}

    def _is_public_mention(
        self,
        observation: Observation,
        schema_types: set[str],
        *,
        request: CollectionRequest | None,
    ) -> bool:
        structural = False
        if observation.entity_type.casefold() in {"publication", "comment", "review"}:
            structural = True
        elif schema_types & self._PUBLICATION_SCHEMA_TYPES:
            structural = True
        elif observation.source_kind.casefold() == "web_page":
            structural = bool(
                (observation.title or "").strip() or (observation.text or "").strip()
            )
        if not structural:
            return False
        return self._territory_supports(observation, "public_mentions", request)

    def _territory_supports(
        self,
        observation: Observation,
        intent: str,
        request: CollectionRequest | None,
    ) -> bool:
        if request is None:
            return True
        if observation.quality.get("territory_relevant") is True:
            return True
        if (
            intent in {"historical_context", "historical_images"}
            and observation.quality.get("historical_territory_relevant") is True
        ):
            return True
        return self.territory_relevance.matches(request, observation)

    @staticmethod
    def _schema_types(observation: Observation) -> list[str]:
        normalization = observation.provenance.get("schema_type_normalization")
        if not isinstance(normalization, dict):
            return []
        values = normalization.get("recognized_types")
        if not isinstance(values, list):
            return []
        return [str(item) for item in values if str(item).strip()]

    @staticmethod
    def _has_archive_provenance(observation: Observation) -> bool:
        archive = observation.provenance.get("archive")
        return isinstance(archive, dict) and archive.get("historical_capture") is True

    @staticmethod
    def _has_historical_image_provenance(observation: Observation) -> bool:
        return (
            observation.quality.get("historical_image") is True
            or observation.provenance.get("historical_image") is True
        )
