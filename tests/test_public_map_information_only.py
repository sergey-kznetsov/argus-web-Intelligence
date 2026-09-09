from __future__ import annotations

import pytest

from argus.contracts.models import Evidence, EvidenceSource, Observation, utcnow
from argus.sources.base import SourceResult, SourceTask
from argus.sources.public_map_web import PublicMapProvenanceWebAdapter


@pytest.mark.parametrize("entity_type", ["document", "review", "comment"])
def test_public_map_observations_remain_evidence_not_messages(entity_type: str) -> None:
    adapter = object.__new__(PublicMapProvenanceWebAdapter)
    adapter.agent = None
    observation = Observation(
        observation_id=f"obs-{entity_type}",
        collection_id="collection-1",
        analysis_id="analysis-1",
        consumer="test",
        source="generic_web",
        source_kind="web_page" if entity_type == "document" else "json_ld",
        url="https://2gis.ru/izhevsk/firm/example",
        entity_type=entity_type,
        text="Source-backed public map information",
        data={},
        content_hash="a" * 64,
        provenance={},
        quality={},
    )
    evidence = Evidence(
        evidence_id=f"ev-{entity_type}",
        observation_id=observation.observation_id,
        type="document",
        text="Source-backed public map information",
        source=EvidenceSource(
            provider="generic_web",
            url=observation.url,
            collected_at=utcnow(),
            source_id="generic_web",
        ),
        metadata={},
    )
    result = SourceResult(observations=[observation], evidence=[evidence])
    task = SourceTask(
        source_id="generic_web",
        goal="reviews",
        url=observation.url,
        metadata={},
    )

    adapter._attach_public_map_provenance(result, task)

    assert observation.quality["public_map_source_identified"] is True
    assert observation.quality["public_map_information_only"] is True
    assert observation.quality["message_candidate"] is False
    assert observation.provenance["public_map_delivery"] == {
        "version": "public-map-information-only/1",
        "information_only": True,
        "message_candidate": False,
        "evidence_preserved": True,
    }
    assert evidence.metadata["public_map_information_only"] is True
    assert evidence.metadata["public_map_delivery"]["evidence_preserved"] is True
    assert len(result.evidence) == 1
