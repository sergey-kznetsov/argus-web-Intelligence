from __future__ import annotations

import hashlib
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from argus.contracts.models import Evidence, Observation
from argus.normalization.identity import stable_evidence_id
from argus.toolpacks import ResolvedToolPack


@dataclass(slots=True)
class ConsumerDeliveryProjector:
    """Apply consumer-selected transport policies without domain interpretation.

    The projector is intentionally technical. It may collapse exact/canonical duplicate
    text and redundant same-page wrapper documents, but it never decides whether a text is
    a complaint, incident, review, historical fact or any other consumer-domain concept.

    ToolPack metadata selects the policy, keeping this behavior consumer-scoped without
    adding consumer IDs to ARGUS Core control flow.
    """

    version: str = "consumer-delivery/1"
    min_dedup_chars: int = 40
    _indexes: dict[str, dict[str, str]] = field(default_factory=dict)

    async def project_task_result(
        self,
        repository,
        *,
        collection_id: str,
        pack: ResolvedToolPack | None,
        observations: list[Observation],
        evidence: list[Evidence],
    ) -> tuple[list[Observation], list[Evidence], dict[str, object]]:
        if (
            pack is None
            or pack.result_delivery_policy != "broad_evidence_stream"
            or pack.result_dedup_policy != "canonical_text_v1"
        ):
            return observations, evidence, {
                "version": self.version,
                "policy": pack.result_delivery_policy if pack is not None else "default",
                "dedup_policy": pack.result_dedup_policy if pack is not None else "none",
                "observations_input": len(observations),
                "observations_output": len(observations),
                "duplicates_collapsed": 0,
            }

        index = await self._index_for(repository, collection_id)
        kept, duplicate_to_canonical = self._deduplicate_batch(observations, index)
        remapped_evidence = self._remap_evidence(evidence, duplicate_to_canonical)
        for observation in kept:
            key = self._dedup_key(observation)
            if key is not None:
                index.setdefault(key, observation.observation_id)

        return kept, remapped_evidence, {
            "version": self.version,
            "policy": pack.result_delivery_policy,
            "dedup_policy": pack.result_dedup_policy,
            "observations_input": len(observations),
            "observations_output": len(kept),
            "duplicates_collapsed": len(duplicate_to_canonical),
            "semantic_filtering_applied": False,
        }

    def release(self, collection_id: str) -> None:
        self._indexes.pop(collection_id, None)

    async def _index_for(self, repository, collection_id: str) -> dict[str, str]:
        cached = self._indexes.get(collection_id)
        if cached is not None:
            return cached
        index: dict[str, str] = {}
        for observation in await repository.list_observations(collection_id):
            key = self._dedup_key(observation)
            if key is not None:
                index.setdefault(key, observation.observation_id)
        self._indexes[collection_id] = index
        return index

    def _deduplicate_batch(
        self,
        observations: list[Observation],
        existing_index: dict[str, str],
    ) -> tuple[list[Observation], dict[str, str]]:
        duplicate_to_canonical: dict[str, str] = {}
        suppressed_by_wrapper = self._same_page_wrapper_duplicates(observations)
        duplicate_to_canonical.update(suppressed_by_wrapper)

        kept: list[Observation] = []
        batch_index: dict[str, Observation] = {}
        for observation in observations:
            if observation.observation_id in duplicate_to_canonical:
                continue
            key = self._dedup_key(observation)
            if key is None:
                kept.append(observation)
                continue

            existing_id = existing_index.get(key)
            if existing_id is not None:
                duplicate_to_canonical[observation.observation_id] = existing_id
                continue

            current = batch_index.get(key)
            if current is None:
                batch_index[key] = observation
                kept.append(observation)
                continue

            preferred, duplicate = self._preferred(current, observation)
            if preferred is current:
                duplicate_to_canonical[observation.observation_id] = current.observation_id
                continue

            duplicate_to_canonical[current.observation_id] = observation.observation_id
            batch_index[key] = observation
            kept = [
                observation if item.observation_id == current.observation_id else item
                for item in kept
            ]

        return kept, duplicate_to_canonical

    def _same_page_wrapper_duplicates(
        self,
        observations: list[Observation],
    ) -> dict[str, str]:
        by_url: dict[str, list[Observation]] = defaultdict(list)
        for observation in observations:
            if observation.url and observation.text and observation.text.strip():
                by_url[observation.url].append(observation)

        duplicate_to_canonical: dict[str, str] = {}
        for items in by_url.values():
            typed = [item for item in items if self._content_rank(item) >= 3]
            wrappers = [
                item
                for item in items
                if item.entity_type.strip().casefold() == "document"
                and item.source_kind.strip().casefold() == "web_page"
            ]
            if len(typed) != 1:
                continue
            representative = typed[0]
            representative_text = self._canonical_text(representative.text or "")
            if len(representative_text) < self.min_dedup_chars:
                continue
            for wrapper in wrappers:
                wrapper_text = self._canonical_text(wrapper.text or "")
                if not wrapper_text or representative_text not in wrapper_text:
                    continue
                # Suppress only a thin page wrapper around one source-declared textual
                # entity. Large pages can contain other useful text and must be preserved.
                if len(wrapper_text) <= max(
                    len(representative_text) + 500,
                    int(len(representative_text) * 1.8),
                ):
                    duplicate_to_canonical[wrapper.observation_id] = (
                        representative.observation_id
                    )
        return duplicate_to_canonical

    def _dedup_key(self, observation: Observation) -> str | None:
        text = self._canonical_text(observation.text or "")
        if len(text) < self.min_dedup_chars:
            return None
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        return f"canonical-text:{digest}"

    @staticmethod
    def _canonical_text(value: str) -> str:
        text = unicodedata.normalize("NFKC", value)
        text = text.replace("\ufeff", "").replace("\u200b", "").replace("\u2060", "")
        return " ".join(text.split()).strip().casefold()

    def _preferred(
        self,
        first: Observation,
        second: Observation,
    ) -> tuple[Observation, Observation]:
        first_rank = self._rank(first)
        second_rank = self._rank(second)
        return (second, first) if second_rank > first_rank else (first, second)

    def _rank(self, observation: Observation) -> tuple[int, int, int, int, int]:
        return (
            self._content_rank(observation),
            1 if observation.published_at is not None else 0,
            1 if observation.geo is not None else 0,
            1 if observation.entity_id else 0,
            1 if observation.title else 0,
        )

    @staticmethod
    def _content_rank(observation: Observation) -> int:
        entity_type = observation.entity_type.strip().casefold()
        source_kind = observation.source_kind.strip().casefold()
        if entity_type in {
            "post",
            "comment",
            "discussion",
            "publication",
            "article",
            "review",
            "complaint",
            "public_appeal",
            "resident_message",
            "local_news_mention",
            "incident_mention",
        }:
            return 4
        if source_kind in {"json_ld", "microdata", "microformat_h_review"}:
            return 3
        if entity_type == "document" and source_kind == "web_page":
            return 1
        return 2

    def _remap_evidence(
        self,
        evidence: Iterable[Evidence],
        duplicate_to_canonical: dict[str, str],
    ) -> list[Evidence]:
        result: list[Evidence] = []
        seen_ids: set[str] = set()
        for item in evidence:
            canonical_id = (
                duplicate_to_canonical.get(item.observation_id or "")
                if item.observation_id
                else None
            )
            if canonical_id is None:
                candidate = item
            else:
                metadata = dict(item.metadata)
                metadata["consumer_delivery_dedup"] = {
                    "version": self.version,
                    "duplicate_observation_id": item.observation_id,
                    "canonical_observation_id": canonical_id,
                    "semantic_filtering_applied": False,
                }
                candidate = item.model_copy(
                    update={
                        "observation_id": canonical_id,
                        "evidence_id": stable_evidence_id(
                            observation_id=canonical_id,
                            evidence_type=item.type,
                            source_url=item.source.url,
                            text=item.text,
                        ),
                        "metadata": metadata,
                    }
                )
            if candidate.evidence_id in seen_ids:
                continue
            seen_ids.add(candidate.evidence_id)
            result.append(candidate)
        return result
