from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field

from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation
from argus.history.snapshots import sha256_text
from argus.normalization.identity import stable_evidence_id, stable_observation_id


@dataclass(slots=True)
class HistoricalDerivation:
    observations: list[Observation] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    changes_seen: int = 0
    truncated: bool = False


class HistoricalTimelineBuilder:
    """Compare two evidence-backed archive captures without semantic inference."""

    version = "historical-timeline/1"
    tracked_data_fields = ("name", "operator", "brand", "former_name", "old_name")

    def __init__(self, *, max_entity_changes: int = 100, max_diff_chars: int = 20_000) -> None:
        self.max_entity_changes = max(1, int(max_entity_changes))
        self.max_diff_chars = max(1_000, int(max_diff_chars))

    def derive(
        self,
        *,
        current: list[Observation],
        previous: list[Observation],
        request: CollectionRequest,
        original_url: str,
        capture_url: str,
        capture_timestamp: str,
        previous_capture_timestamp: str | None,
    ) -> HistoricalDerivation:
        result = HistoricalDerivation()
        current_primary = self._primary(current)
        previous_primary = self._primary(previous)
        if current_primary is not None:
            page_observation, page_evidence = self._page_version(
                current_primary=current_primary,
                previous_primary=previous_primary,
                request=request,
                original_url=original_url,
                capture_url=capture_url,
                capture_timestamp=capture_timestamp,
                previous_capture_timestamp=previous_capture_timestamp,
            )
            result.observations.append(page_observation)
            result.evidence.append(page_evidence)

        if not previous:
            return result

        current_entities = self._entity_map(current)
        previous_entities = self._entity_map(previous)
        all_keys = sorted(set(current_entities) | set(previous_entities))
        changes: list[tuple[str, dict[str, object], Observation | None, Observation | None]] = []
        for key in all_keys:
            current_item = current_entities.get(key)
            previous_item = previous_entities.get(key)
            if previous_item is None and current_item is not None:
                changes.append(
                    (
                        "appeared_between_captures",
                        {},
                        None,
                        current_item,
                    )
                )
                continue
            if current_item is None and previous_item is not None:
                changes.append(
                    (
                        "disappeared_between_captures",
                        {},
                        previous_item,
                        None,
                    )
                )
                continue
            assert current_item is not None and previous_item is not None
            field_changes = self._field_changes(previous_item, current_item)
            if field_changes:
                changes.append(
                    (
                        "fields_changed",
                        field_changes,
                        previous_item,
                        current_item,
                    )
                )

        result.changes_seen = len(changes)
        if len(changes) > self.max_entity_changes:
            result.truncated = True
            changes = changes[: self.max_entity_changes]

        for change_type, field_changes, previous_item, current_item in changes:
            observation, evidence = self._entity_change(
                change_type=change_type,
                field_changes=field_changes,
                previous_item=previous_item,
                current_item=current_item,
                request=request,
                original_url=original_url,
                capture_url=capture_url,
                capture_timestamp=capture_timestamp,
                previous_capture_timestamp=previous_capture_timestamp,
            )
            result.observations.append(observation)
            result.evidence.append(evidence)
        return result

    def _page_version(
        self,
        *,
        current_primary: Observation,
        previous_primary: Observation | None,
        request: CollectionRequest,
        original_url: str,
        capture_url: str,
        capture_timestamp: str,
        previous_capture_timestamp: str | None,
    ) -> tuple[Observation, Evidence]:
        current_text = current_primary.text or ""
        previous_text = previous_primary.text if previous_primary is not None else ""
        changed = (
            previous_primary is not None
            and previous_primary.content_hash != current_primary.content_hash
        )
        diff = ""
        if changed:
            diff = "\n".join(
                difflib.unified_diff(
                    (previous_text or "").splitlines(),
                    current_text.splitlines(),
                    fromfile=previous_capture_timestamp or "previous",
                    tofile=capture_timestamp,
                    lineterm="",
                )
            )[: self.max_diff_chars]

        facts = {
            "timeline_version": self.version,
            "change_type": (
                "first_observed_capture"
                if previous_primary is None
                else "page_content_changed" if changed else "page_content_unchanged"
            ),
            "original_url": original_url,
            "capture_url": capture_url,
            "capture_timestamp": capture_timestamp,
            "previous_capture_timestamp": previous_capture_timestamp,
            "current_observation_id": current_primary.observation_id,
            "previous_observation_id": (
                previous_primary.observation_id if previous_primary is not None else None
            ),
            "current_content_hash": current_primary.content_hash,
            "previous_content_hash": (
                previous_primary.content_hash if previous_primary is not None else None
            ),
            "content_changed": changed if previous_primary is not None else None,
            "diff": diff or None,
        }
        canonical = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content_hash = sha256_text(canonical)
        entity_id = f"{original_url}:{capture_timestamp}:page"
        observation_id = stable_observation_id(
            collection_id=current_primary.collection_id,
            source_id=current_primary.source,
            entity_type="historical_page_version",
            entity_id=entity_id,
            source_url=capture_url,
            content_hash=content_hash,
        )
        observation = Observation(
            observation_id=observation_id,
            collection_id=current_primary.collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=current_primary.source,
            source_kind="historical_page_version",
            url=capture_url,
            entity_type="historical_page_version",
            entity_id=entity_id,
            title=f"Historical page version: {original_url}",
            text=diff or facts["change_type"],
            data=facts,
            content_hash=content_hash,
            provenance={
                "historical": {
                    "version": self.version,
                    "original_url": original_url,
                    "capture_timestamp": capture_timestamp,
                    "previous_capture_timestamp": previous_capture_timestamp,
                    "derived_from_observations": [
                        value
                        for value in (
                            previous_primary.observation_id if previous_primary else None,
                            current_primary.observation_id,
                        )
                        if value
                    ],
                    "semantic_inference": False,
                }
            },
            quality={
                "evidence_backed": True,
                "derived_from_evidence": True,
                "semantic_inference": False,
            },
        )
        evidence_text = canonical[:10_000]
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation_id,
                evidence_type="historical_comparison",
                source_url=capture_url,
                text=evidence_text,
            ),
            observation_id=observation_id,
            type="historical_comparison",
            text=evidence_text,
            source=EvidenceSource(
                provider=current_primary.source,
                url=capture_url,
                collected_at=observation.collected_at,
                source_id=current_primary.source,
            ),
            metadata={
                "timeline_version": self.version,
                "current_observation_id": current_primary.observation_id,
                "previous_observation_id": (
                    previous_primary.observation_id if previous_primary is not None else None
                ),
                "derived_comparison": True,
                "semantic_inference": False,
            },
        )
        return observation, evidence

    def _entity_change(
        self,
        *,
        change_type: str,
        field_changes: dict[str, object],
        previous_item: Observation | None,
        current_item: Observation | None,
        request: CollectionRequest,
        original_url: str,
        capture_url: str,
        capture_timestamp: str,
        previous_capture_timestamp: str | None,
    ) -> tuple[Observation, Evidence]:
        representative = current_item or previous_item
        assert representative is not None
        entity_key = self._entity_key(representative) or representative.observation_id
        facts = {
            "timeline_version": self.version,
            "change_type": change_type,
            "entity_key": entity_key,
            "entity_type": representative.entity_type,
            "source_kind": representative.source_kind,
            "original_url": original_url,
            "capture_url": capture_url,
            "capture_timestamp": capture_timestamp,
            "previous_capture_timestamp": previous_capture_timestamp,
            "previous_observation_id": previous_item.observation_id if previous_item else None,
            "current_observation_id": current_item.observation_id if current_item else None,
            "field_changes": field_changes,
        }
        canonical = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content_hash = sha256_text(canonical)
        entity_id = (
            f"{original_url}:{previous_capture_timestamp or 'none'}:"
            f"{capture_timestamp}:{change_type}:{entity_key}"
        )
        observation_id = stable_observation_id(
            collection_id=representative.collection_id,
            source_id=representative.source,
            entity_type="historical_entity_change",
            entity_id=entity_id,
            source_url=capture_url,
            content_hash=content_hash,
        )
        label = representative.title or self._field(representative, "name") or entity_key
        observation = Observation(
            observation_id=observation_id,
            collection_id=representative.collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=representative.source,
            source_kind="historical_entity_change",
            url=capture_url,
            entity_type="historical_entity_change",
            entity_id=entity_id,
            title=f"Historical change: {label}",
            text=canonical[:10_000],
            data=facts,
            content_hash=content_hash,
            provenance={
                "historical": {
                    "version": self.version,
                    "original_url": original_url,
                    "capture_timestamp": capture_timestamp,
                    "previous_capture_timestamp": previous_capture_timestamp,
                    "derived_from_observations": [
                        value
                        for value in (
                            previous_item.observation_id if previous_item else None,
                            current_item.observation_id if current_item else None,
                        )
                        if value
                    ],
                    "semantic_inference": False,
                }
            },
            quality={
                "evidence_backed": True,
                "derived_from_evidence": True,
                "semantic_inference": False,
            },
        )
        evidence_text = canonical[:10_000]
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation_id,
                evidence_type="historical_comparison",
                source_url=capture_url,
                text=evidence_text,
            ),
            observation_id=observation_id,
            type="historical_comparison",
            text=evidence_text,
            source=EvidenceSource(
                provider=representative.source,
                url=capture_url,
                collected_at=observation.collected_at,
                source_id=representative.source,
            ),
            metadata={
                "timeline_version": self.version,
                "previous_observation_id": previous_item.observation_id if previous_item else None,
                "current_observation_id": current_item.observation_id if current_item else None,
                "derived_comparison": True,
                "semantic_inference": False,
            },
        )
        return observation, evidence

    def _entity_map(self, observations: list[Observation]) -> dict[str, Observation]:
        result: dict[str, Observation] = {}
        for observation in observations:
            if observation.source_kind in {
                "web_page",
                "historical_page_version",
                "historical_entity_change",
                "archive_capture_index",
            }:
                continue
            key = self._entity_key(observation)
            if key and key not in result:
                result[key] = observation
        return result

    def _entity_key(self, observation: Observation) -> str | None:
        if observation.entity_id:
            return f"{observation.entity_type}:id:{observation.entity_id}"
        name = self._field(observation, "name") or observation.title
        if not isinstance(name, str):
            return None
        normalized = " ".join(name.casefold().split())[:256]
        if not normalized:
            return None
        return f"{observation.entity_type}:name:{normalized}"

    def _field_changes(
        self,
        previous: Observation,
        current: Observation,
    ) -> dict[str, object]:
        changes: dict[str, object] = {}
        for field_name in ("title", *self.tracked_data_fields):
            before = previous.title if field_name == "title" else self._field(previous, field_name)
            after = current.title if field_name == "title" else self._field(current, field_name)
            if before == after:
                continue
            if before is None and after is None:
                continue
            changes[field_name] = {"from": before, "to": after}
        return changes

    @staticmethod
    def _field(observation: Observation, name: str) -> str | None:
        value = observation.data.get(name)
        if isinstance(value, str):
            normalized = " ".join(value.split())[:2_000]
            return normalized or None
        return None

    @staticmethod
    def _primary(observations: list[Observation]) -> Observation | None:
        for observation in observations:
            if observation.source_kind == "web_page":
                return observation
        return None
