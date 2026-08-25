from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from argus.contracts.models import Evidence, Observation, Snapshot


class ProvenanceQualityNormalizer:
    """Attach uniform, evidence-only provenance/quality facts before atomic persistence.

    This normalizer deliberately does not assign semantic truth confidence. It records
    only technical facts that a downstream consumer can inspect when deciding how much
    weight to give an Observation.
    """

    provenance_version = "argus-provenance/1"
    quality_version = "evidence-quality/1"
    max_goals = 50
    max_goal_chars = 256

    def normalize(
        self,
        observations: list[Observation],
        evidence: list[Evidence],
        snapshots: list[Snapshot],
    ) -> None:
        snapshots_by_id = {item.snapshot_id: item for item in snapshots}
        evidence_by_observation: dict[str, list[Evidence]] = defaultdict(list)
        for item in evidence:
            if item.observation_id:
                evidence_by_observation[item.observation_id].append(item)

        for observation in observations:
            linked_evidence = evidence_by_observation.get(observation.observation_id, [])
            snapshot_id = self._snapshot_id(observation)
            snapshot = snapshots_by_id.get(snapshot_id) if snapshot_id else None
            goals = self._research_goals(observation)
            runtime = self._runtime(observation)
            extractor_version = self._extractor_version(observation, snapshot)
            discovery = self._bounded_mapping(
                observation.provenance.get("discovery_navigation")
                or observation.provenance.get("discovery")
            )

            provenance: dict[str, Any] = {
                "version": self.provenance_version,
                "source_id": observation.source,
                "source_kind": observation.source_kind,
                "source_url": observation.url,
                "collection_id": observation.collection_id,
                "analysis_id": observation.analysis_id,
                "consumer": observation.consumer,
                "collected_at": observation.collected_at.isoformat(),
                "observation_content_hash": observation.content_hash,
                "research_goals": goals,
                "runtime": runtime,
                "extractor_version": extractor_version,
                "snapshot_id": snapshot_id,
                "discovery": discovery,
            }
            if snapshot is not None:
                provenance["snapshot"] = {
                    "snapshot_id": snapshot.snapshot_id,
                    "source_id": snapshot.source_id,
                    "source_url": snapshot.source_url,
                    "collected_at": snapshot.collected_at.isoformat(),
                    "content_hash": snapshot.content_hash,
                    "extractor_version": snapshot.extractor_version,
                    "content_type": snapshot.content_type,
                }
            observation.provenance["argus"] = provenance

            source_url_matches = bool(linked_evidence) and all(
                item.source.url == observation.url for item in linked_evidence
            )
            partial = self._partial(observation)
            machine_readable = self._machine_readable(observation)
            duplicate = bool(observation.quality.get("duplicate_content"))
            quality_evidence = {
                "version": self.quality_version,
                "truth_confidence_assigned": False,
                "evidence_backed": bool(linked_evidence),
                "evidence_count": len(linked_evidence),
                "snapshot_backed": snapshot_id is not None,
                "snapshot_available_in_task_commit": snapshot is not None,
                "content_hash_present": bool(observation.content_hash.strip()),
                "machine_readable": machine_readable,
                "partial": partial,
                "duplicate_content": duplicate,
                "evidence_source_url_matches_observation": source_url_matches,
            }
            observation.quality["evidence_quality"] = quality_evidence

            for item in linked_evidence:
                item.metadata["argus_provenance"] = {
                    "version": self.provenance_version,
                    "observation_id": observation.observation_id,
                    "observation_content_hash": observation.content_hash,
                    "source_id": observation.source,
                    "source_kind": observation.source_kind,
                    "source_url": item.source.url,
                    "collected_at": item.source.collected_at.isoformat(),
                    "snapshot_id": snapshot_id,
                    "extractor_version": extractor_version,
                    "runtime": runtime,
                    "research_goals": goals,
                    "discovery": discovery,
                    "evidence_text_sha256": self._text_hash(item.text),
                    "truth_confidence_assigned": False,
                }

    @staticmethod
    def _snapshot_id(observation: Observation) -> str | None:
        value = observation.provenance.get("snapshot_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        document = observation.provenance.get("document")
        if isinstance(document, dict):
            value = document.get("snapshot_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _research_goals(self, observation: Observation) -> list[str]:
        raw = observation.provenance.get("research_goals")
        if not isinstance(raw, list):
            raw = observation.data.get("research_goals")
        if not isinstance(raw, list):
            return []
        goals: list[str] = []
        seen: set[str] = set()
        for value in raw[: self.max_goals]:
            goal = str(value).strip()[: self.max_goal_chars]
            if not goal or goal in seen:
                continue
            seen.add(goal)
            goals.append(goal)
        return goals

    @staticmethod
    def _runtime(observation: Observation) -> str | None:
        value = observation.data.get("runtime")
        if isinstance(value, str) and value.strip():
            return value.strip()[:128]
        fetch_metadata = observation.data.get("fetch_metadata")
        if isinstance(fetch_metadata, dict):
            value = fetch_metadata.get("runtime")
            if isinstance(value, str) and value.strip():
                return value.strip()[:128]
        return None

    @staticmethod
    def _extractor_version(
        observation: Observation,
        snapshot: Snapshot | None,
    ) -> str | None:
        if snapshot is not None and snapshot.extractor_version:
            return snapshot.extractor_version[:256]
        value = observation.data.get("extractor_version")
        if isinstance(value, str) and value.strip():
            return value.strip()[:256]
        document = observation.provenance.get("document")
        if isinstance(document, dict):
            value = document.get("extractor_version")
            if isinstance(value, str) and value.strip():
                return value.strip()[:256]
        return None

    @staticmethod
    def _partial(observation: Observation) -> bool:
        if bool(observation.quality.get("partial")):
            return True
        if bool(observation.data.get("truncated")):
            return True
        for key in (
            "json_ld_summary",
            "page_metadata_summary",
            "microformats_summary",
            "html_table_summary",
            "microdata_summary",
            "geojson_summary",
            "kml_summary",
        ):
            value = observation.data.get(key)
            if isinstance(value, dict) and bool(value.get("truncated")):
                return True
        return False

    @staticmethod
    def _machine_readable(observation: Observation) -> bool:
        if bool(observation.quality.get("machine_readable")):
            return True
        return observation.source_kind in {
            "json_ld",
            "microdata",
            "microformat_entry",
            "microformat_review",
            "page_metadata",
            "html_table",
            "structured_data",
            "office_spreadsheet",
            "json_feed_item",
            "rss_item",
            "atom_entry",
            "geojson_point",
            "kml_point",
            "map_feature",
        }

    @staticmethod
    def _bounded_mapping(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:32]:
            normalized_key = str(key)[:128]
            if item is None or isinstance(item, (bool, int, float)):
                result[normalized_key] = item
            elif isinstance(item, str):
                result[normalized_key] = item[:2_000]
            elif isinstance(item, list):
                result[normalized_key] = [str(part)[:512] for part in item[:32]]
        return result or None

    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
