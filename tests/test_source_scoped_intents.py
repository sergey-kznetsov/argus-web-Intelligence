from __future__ import annotations

import pytest

from argus.contracts.models import CollectionRequest
from argus.research.source_scoped_intents import SourceScopedIntentEvidenceClassifier
from argus.sources.base import SourceResult


class _Classifier:
    version = "delegate/1"
    supported_intents = {"*"}
    marker_required_intents = set()

    def __init__(self) -> None:
        self.received: list[str] | None = None

    async def annotate(self, request, result):
        self.received = list(request.intents)
        return result


def _request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="source-scope-test",
        analysis_id="a1",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=list(intents),
    )


@pytest.mark.asyncio
async def test_generic_classifier_never_receives_residential_only_intents():
    delegate = _Classifier()
    classifier = SourceScopedIntentEvidenceClassifier(
        delegate,
        source_scoped_intents={"residential_population", "residential_premises_count"},
    )
    result = SourceResult(observations=[])

    returned = await classifier.annotate(
        _request("residential_population", "residential_premises_count"),
        result,
    )

    assert returned is result
    assert delegate.received is None


@pytest.mark.asyncio
async def test_mixed_request_passes_only_unscoped_intents_to_generic_classifier():
    delegate = _Classifier()
    classifier = SourceScopedIntentEvidenceClassifier(
        delegate,
        source_scoped_intents={"residential_population", "residential_premises_count"},
    )

    await classifier.annotate(
        _request("residential_population", "local_news", "complaints"),
        SourceResult(observations=[]),
    )

    assert delegate.received == ["local_news", "complaints"]
    assert classifier.supported_intents == {"*"}
