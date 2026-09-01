from datetime import UTC, datetime

from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation
from argus.normalization.territory_relevance import TerritoryRelevanceProofNormalizer


def _request(address: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="test",
        analysis_id="territory-proof",
        territory={"city": "Ижевск", "address": address},
        intents=["public_mentions"],
    )


def _observation(text: str) -> Observation:
    return Observation(
        observation_id="observation-proof",
        collection_id="collection-proof",
        analysis_id="territory-proof",
        consumer="test",
        source="generic_web",
        source_kind="json_ld",
        url="https://example.test/post",
        entity_type="post",
        text=text,
        content_hash="c" * 64,
    )


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="evidence-proof",
        observation_id="observation-proof",
        type="structured_field",
        text="Ижевск, улица Пушкинская, дом 277",
        source=EvidenceSource(
            provider="generic_web",
            url="https://example.test/post",
            collected_at=datetime(2026, 8, 30, 18, 30, tzinfo=UTC),
        ),
    )


def test_exact_address_is_persisted_on_observation_and_linked_evidence() -> None:
    observation = _observation(
        "Ижевск, улица Пушкинская, дом 277. Жители сообщают о проблеме."
    )
    evidence = _evidence()

    TerritoryRelevanceProofNormalizer().normalize(
        _request("Ижевск, улица Пушкинская, дом 277"),
        [observation],
        [evidence],
    )

    proof = observation.provenance["territory_relevance"]
    assert observation.quality["territory_relevant"] is True
    assert proof["matched"] is True
    assert proof["basis"] == "exact_address"
    assert proof["source_backed"] is True
    assert proof["planning_metadata_used_as_evidence"] is False
    assert evidence.metadata["territory_relevance_verified"] is True
    assert evidence.metadata["territory_relevance"]["matched"] is True
    assert evidence.metadata["territory_relevance"]["basis"] == "exact_address"


def test_different_address_is_persisted_as_not_relevant() -> None:
    observation = _observation(
        "Ижевск, улица Ленина, дом 10. Жители сообщают о проблеме."
    )
    evidence = _evidence()

    TerritoryRelevanceProofNormalizer().normalize(
        _request("Ижевск, улица Пушкинская, дом 277"),
        [observation],
        [evidence],
    )

    proof = observation.provenance["territory_relevance"]
    assert observation.quality["territory_relevant"] is False
    assert proof["matched"] is False
    assert proof["basis"] == "address_anchor_missing"
    assert evidence.metadata["territory_relevance_verified"] is False
    assert evidence.metadata["territory_relevance"]["matched"] is False
