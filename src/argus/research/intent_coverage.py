from __future__ import annotations

from collections.abc import Iterable

from argus.contracts.models import Observation
from argus.research.url_identity import canonicalize_discovery_url


class IntentCoverageEvaluator:
    """Determine achieved research coverage from factual observations.

    ``research_goals`` describe why ARGUS visited a source. They are navigation and
    planning metadata, not proof that the source actually contained the requested
    fact. Coverage is therefore credited only from factual/source-declared shapes or
    an explicit ``intent_evidence`` quality marker.
    """

    version = "intent-evidence-coverage/2"
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

    def supports(self, observation: Observation, intent: str) -> bool:
        normalized = intent.strip().casefold()
        if not normalized:
            return False

        explicit = observation.quality.get("intent_evidence")
        if isinstance(explicit, dict) and explicit.get(normalized) is True:
            return True
        if isinstance(explicit, list) and normalized in {
            str(item).strip().casefold() for item in explicit
        }:
            return True

        entity_type = observation.entity_type.casefold().strip()
        source_kind = observation.source_kind.casefold().strip()
        schema_types = set(self._schema_types(observation))

        if normalized == "reviews":
            return entity_type == "review"
        if normalized == "comments":
            return entity_type == "comment"
        if normalized == "discussions":
            return entity_type == "comment" or "DiscussionForumPosting" in schema_types
        if normalized == "local_news":
            return (
                "NewsArticle" in schema_types
                or source_kind in {"feed_entry", "json_feed_item"}
            )
        if normalized == "public_mentions":
            return self._is_public_mention(observation, schema_types)
        if normalized == "historical_context":
            return (
                source_kind in self._HISTORICAL_SOURCE_KINDS
                or self._has_archive_provenance(observation)
            )
        if normalized in {"images", "historical_images"}:
            return entity_type == "image" or source_kind == "image_reference"

        # Incidents and complaints require semantic relevance, not merely a page that
        # happened to be fetched for that goal. They remain uncovered until a factual
        # extractor/classifier emits an explicit intent_evidence marker.
        return False

    def counts(self, observations: Iterable[Observation]) -> dict[str, int]:
        urls_by_intent: dict[str, set[str]] = {}
        for observation in observations:
            source_identity = self._source_identity(observation)
            for intent in self._candidate_intents(observation):
                if self.supports(observation, intent):
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

    @classmethod
    def _is_public_mention(
        cls,
        observation: Observation,
        schema_types: set[str],
    ) -> bool:
        if observation.entity_type.casefold() in {"publication", "comment", "review"}:
            return True
        if schema_types & cls._PUBLICATION_SCHEMA_TYPES:
            return True
        if observation.source_kind.casefold() == "web_page":
            return bool((observation.title or "").strip() or (observation.text or "").strip())
        return False

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
