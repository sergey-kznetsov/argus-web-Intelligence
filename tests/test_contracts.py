import pytest
from pydantic import ValidationError

from argus.contracts.models import CollectionRequest


def test_collection_request_accepts_address():
    req = CollectionRequest(consumer="kraken.development.uds", analysis_id="123",
                            territory={"city": "Ижевск", "address": "Пушкинская, 277"},
                            intents=["reviews"])
    assert req.protocol_version == "1.0.0"
    assert req.territory.city == "Ижевск"


def test_territory_requires_locator():
    with pytest.raises(ValidationError):
        CollectionRequest(consumer="x", analysis_id="1", territory={}, intents=["reviews"])
