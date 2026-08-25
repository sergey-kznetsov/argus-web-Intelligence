from __future__ import annotations

import hashlib

from argus.contracts.models import Evidence, EvidenceSource, Observation, Snapshot, utcnow
from argus.normalization.provenance_quality import ProvenanceQualityNormalizer


def observation() -> Observation:
    return Observation(
        observation_id="obs-1",
        collection_id="collection-1",
        analysis_id="analysis-1",
        consumer="test-consumer",
        source="generic_web",
        source_kind="json_ld",
        url="https://example.com/article",
        entity_type="publication",
        text="Evidence-backed article",
        data={
            "runtime": "browser_recipe",
            "research_goals": ["local_news", "local_news", "public_mentions"],
        },
        content_hash="a" * 64,
        provenance={
            "snapshot_id": "snapshot-1",
            "research_goals": ["local_news", "public_mentions"],
            "discovery_navigation": {
                "provider": "searxng",
                "rank": 1,
                "navigation_only": True,
                "is_evidence": False,
            },
        },
        quality={"evidence_backed": True, "machine_readable": True},
    )


def evidence(obs: Observation) -> Evidence:
    return Evidence(
        evidence_id="evidence-1",
        observation_id=obs.observation_id,
        type="json_ld",
        text='{"headline":"Evidence-backed article"}',
        source=EvidenceSource(
            provider="generic_web",
            url=obs.url,
            collected_at=utcnow(),
            source_id="generic_web",
        ),
    )


def snapshot() -> Snapshot:
    return Snapshot(
        snapshot_id="snapshot-1",
        source_id="generic_web",
        source_url="https://example.com/article",
        content_hash="b" * 64,
        extractor_version="argus/0.3.0",
        content_type="text/html",
        content="<html>source</html>",
    )


def test_normalizer_attaches_uniform_provenance_without_truth_confidence():
    obs = observation()
    item = evidence(obs)
    snap = snapshot()
    normalizer = ProvenanceQualityNormalizer()

    normalizer.normalize([obs], [item], [snap])

    provenance = obs.provenance["argus"]
    assert provenance["version"] == "argus-provenance/1"
    assert provenance["source_id"] == "generic_web"
    assert provenance["source_kind"] == "json_ld"
    assert provenance["source_url"] == obs.url
    assert provenance["observation_content_hash"] == obs.content_hash
    assert provenance["runtime"] == "browser_recipe"
    assert provenance["research_goals"] == ["local_news", "public_mentions"]
    assert provenance["snapshot"]["snapshot_id"] == "snapshot-1"
    assert provenance["snapshot"]["extractor_version"] == "argus/0.3.0"
    assert provenance["discovery"]["navigation_only"] is True
    assert provenance["discovery"]["is_evidence"] is False

    quality = obs.quality["evidence_quality"]
    assert quality["version"] == "evidence-quality/1"
    assert quality["truth_confidence_assigned"] is False
    assert quality["evidence_backed"] is True
    assert quality["evidence_count"] == 1
    assert quality["snapshot_backed"] is True
    assert quality["snapshot_available_in_task_commit"] is True
    assert quality["machine_readable"] is True
    assert quality["partial"] is False
    assert quality["evidence_source_url_matches_observation"] is True
    assert "score" not in quality
    assert "confidence" not in quality

    evidence_provenance = item.metadata["argus_provenance"]
    assert evidence_provenance["observation_id"] == obs.observation_id
    assert evidence_provenance["observation_content_hash"] == obs.content_hash
    assert evidence_provenance["truth_confidence_assigned"] is False
    assert evidence_provenance["evidence_text_sha256"] == hashlib.sha256(
        item.text.encode("utf-8")
    ).hexdigest()


def test_existing_snapshot_reference_is_preserved_when_not_recommitted():
    obs = observation()
    item = evidence(obs)

    ProvenanceQualityNormalizer().normalize([obs], [item], [])

    quality = obs.quality["evidence_quality"]
    assert quality["snapshot_backed"] is True
    assert quality["snapshot_available_in_task_commit"] is False
    assert obs.provenance["argus"]["snapshot_id"] == "snapshot-1"
    assert "snapshot" not in obs.provenance["argus"]


def test_partial_and_duplicate_are_reported_as_quality_evidence_not_confidence():
    obs = observation()
    obs.source_kind = "web_page"
    obs.quality["partial"] = True
    obs.quality["duplicate_content"] = True
    obs.data["truncated"] = True
    item = evidence(obs)

    ProvenanceQualityNormalizer().normalize([obs], [item], [])

    quality = obs.quality["evidence_quality"]
    assert quality["partial"] is True
    assert quality["duplicate_content"] is True
    assert quality["truth_confidence_assigned"] is False


def test_unlinked_evidence_does_not_make_observation_evidence_backed():
    obs = observation()
    item = evidence(obs)
    item.observation_id = "other-observation"

    ProvenanceQualityNormalizer().normalize([obs], [item], [])

    quality = obs.quality["evidence_quality"]
    assert quality["evidence_backed"] is False
    assert quality["evidence_count"] == 0
    assert "argus_provenance" not in item.metadata
