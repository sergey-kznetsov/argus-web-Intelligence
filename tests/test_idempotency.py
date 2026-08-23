import pytest
from pydantic import ValidationError

from argus.contracts.models import CollectionRequest
from argus.idempotency import request_fingerprint, storage_idempotency_key


def request(**updates) -> CollectionRequest:
    payload = {
        "consumer": "kraken",
        "analysis_id": "analysis-1",
        "territory": {"city": "Ижевск", "address": "Пушкинская, 277"},
        "intents": ["public_mentions", "local_news"],
    }
    payload.update(updates)
    return CollectionRequest(**payload)


def test_fingerprint_ignores_transport_idempotency_key():
    without_key = request()
    with_key = request(idempotency_key=" retry-1 ")

    assert with_key.idempotency_key == "retry-1"
    assert request_fingerprint(without_key) == request_fingerprint(with_key)


def test_explicit_storage_key_is_scoped_by_consumer():
    kraken = request(idempotency_key="same-key")
    janus = request(consumer="janus", idempotency_key="same-key")

    kraken_hash = request_fingerprint(kraken)
    janus_hash = request_fingerprint(janus)
    assert storage_idempotency_key(kraken, kraken_hash) != storage_idempotency_key(
        janus, janus_hash
    )


def test_auto_storage_key_changes_with_factual_request_payload():
    first = request()
    second = request(intents=["public_mentions"])

    first_hash = request_fingerprint(first)
    second_hash = request_fingerprint(second)
    assert first_hash != second_hash
    assert storage_idempotency_key(first, first_hash) != storage_idempotency_key(
        second, second_hash
    )


def test_blank_idempotency_key_is_rejected():
    with pytest.raises(ValidationError):
        request(idempotency_key="   ")
