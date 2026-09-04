from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.research.source_scoped_intents import SourceScopedIntentEvidenceClassifier
from argus.sources.base import SourceResult


class _Delegate:
    supported_intents = {"*"}
    marker_required_intents = set()

    def __init__(self) -> None:
        self.received: list[str] | None = None

    async def annotate(self, request, result):
        self.received = list(request.intents)
        return result


class _Health:
    def __init__(self, ready: bool) -> None:
        self.ready = ready
        self.calls = 0

    async def check(self):
        self.calls += 1
        return SimpleNamespace(ready=self.ready)


def _request() -> CollectionRequest:
    return CollectionRequest(
        consumer="generic-open-web-test",
        analysis_id="generic-health-gate",
        territory={"city": "Ижевск", "address": "Пушкинская, 277"},
        intents=["local_news", "complaints"],
    )


@pytest.mark.asyncio
async def test_unavailable_optional_llm_skips_generic_model_assisted_evidence():
    delegate = _Delegate()
    health = _Health(False)
    classifier = SourceScopedIntentEvidenceClassifier(
        delegate,  # type: ignore[arg-type]
        source_scoped_intents=set(),
        llm_health=health,  # type: ignore[arg-type]
    )
    result = SourceResult(observations=[])

    returned = await classifier.annotate(_request(), result)

    assert returned is result
    assert health.calls == 1
    assert delegate.received is None


@pytest.mark.asyncio
async def test_ready_optional_llm_keeps_generic_model_assisted_evidence_enabled():
    delegate = _Delegate()
    health = _Health(True)
    classifier = SourceScopedIntentEvidenceClassifier(
        delegate,  # type: ignore[arg-type]
        source_scoped_intents=set(),
        llm_health=health,  # type: ignore[arg-type]
    )
    result = SourceResult(observations=[])

    returned = await classifier.annotate(_request(), result)

    assert returned is result
    assert health.calls == 1
    assert delegate.received == ["local_news", "complaints"]
