import pytest
from pydantic import ValidationError

from argus.consumer_registry import consumer_profile_catalog
from argus.contracts.models import CollectionRequest


def base_request(**overrides):
    payload = {
        "consumer": "test",
        "analysis_id": "consumer-contract-test",
        "territory": {
            "city": "Ижевск",
            "address": "Ижевск, Пушкинская, 277",
        },
        "intents": ["public_mentions"],
    }
    payload.update(overrides)
    return CollectionRequest(**payload)


def test_kraken_profile_resolves_default_capability_and_facts():
    request = base_request(consumer="kraken.development.uds")

    assert request.consumer == "kraken.development.uds"
    assert request.consumer_profile_version == 1
    assert request.capability == "urban_signals"
    assert request.tool_pack_id == "kraken.urban_signals"
    assert request.tool_pack_version == 1
    assert request.requested_facts == [
        "complaint",
        "public_appeal",
        "post",
        "comment",
        "resident_message",
        "local_news_mention",
        "incident_mention",
    ]


def test_profiled_consumer_can_request_supported_fact_subset():
    request = base_request(
        consumer="kraken.development.uds",
        consumer_profile_version=1,
        capability="urban-signals",
        requested_facts=["complaint", "comment", "complaint"],
    )

    assert request.capability == "urban_signals"
    assert request.requested_facts == ["complaint", "comment"]
    assert request.tool_pack_id == "kraken.urban_signals"
    assert request.tool_pack_version == 1


def test_profiled_consumer_rejects_business_review_fact():
    with pytest.raises(ValidationError, match="UNSUPPORTED_REQUESTED_FACT"):
        base_request(
            consumer="kraken.development.uds",
            requested_facts=["review"],
        )


def test_profiled_consumer_rejects_unknown_capability():
    with pytest.raises(ValidationError, match="UNSUPPORTED_CAPABILITY"):
        base_request(
            consumer="kraken.development.uds",
            capability="building_demographics",
        )


def test_profiled_consumer_rejects_unsupported_fact():
    with pytest.raises(ValidationError, match="UNSUPPORTED_REQUESTED_FACT"):
        base_request(
            consumer="kraken.development.uds",
            requested_facts=["apartment_count"],
        )


def test_profiled_consumer_rejects_wrong_profile_version():
    with pytest.raises(ValidationError, match="UNSUPPORTED_CONSUMER_PROFILE_VERSION"):
        base_request(
            consumer="kraken.development.uds",
            consumer_profile_version=2,
        )


def test_unknown_consumer_is_legacy_compatible_without_profile_fields():
    request = base_request(consumer="legacy-module-under-migration")

    assert request.consumer == "legacy-module-under-migration"
    assert request.consumer_profile_version is None
    assert request.capability is None
    assert request.requested_facts == []
    assert request.tool_pack_id is None
    assert request.tool_pack_version is None


def test_unknown_consumer_cannot_use_profiled_contract_fields():
    with pytest.raises(ValidationError, match="UNKNOWN_CONSUMER"):
        base_request(
            consumer="unregistered.module",
            capability="urban_signals",
        )


def test_catalog_exposes_kraken_module_contract():
    kraken = next(
        item
        for item in consumer_profile_catalog()
        if item["consumer_id"] == "kraken.development.uds"
    )

    assert kraken["version"] == 1
    assert kraken["default_capability"] == "urban_signals"
    capability = kraken["capabilities"][0]
    assert capability["capability"] == "urban_signals"
    assert capability["tool_pack_id"] == "kraken.urban_signals"
    assert "complaint" in capability["allowed_facts"]
    assert "review" not in capability["allowed_facts"]
