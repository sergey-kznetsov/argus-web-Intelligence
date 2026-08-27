from __future__ import annotations

from collections.abc import Iterable

from argus.contracts.models import CollectionRequest
from argus.research.intent_evidence import OllamaIntentEvidenceClassifier
from argus.sources.base import SourceResult


class SourceScopedIntentEvidenceClassifier:
    """Prevent the generic semantic classifier from proving source-scoped intents.

    Dedicated adapters own the factual contract for these intents. The wrapper removes them
    before an untrusted generic page reaches the local LLM classifier, so a mixed collection
    cannot accidentally satisfy a source-specific requirement with an alternative website.
    Other intents preserve the existing exact-excerpt semantic pipeline unchanged.
    """

    version = "source-scoped-intent-evidence/1"

    def __init__(
        self,
        delegate: OllamaIntentEvidenceClassifier,
        *,
        source_scoped_intents: Iterable[str],
    ) -> None:
        self.delegate = delegate
        self.source_scoped_intents = frozenset(
            str(item).strip().casefold()
            for item in source_scoped_intents
            if str(item).strip()
        )

    async def annotate(self, request: CollectionRequest, result: SourceResult) -> SourceResult:
        generic_intents = [
            intent
            for intent in request.intents
            if str(intent).strip().casefold() not in self.source_scoped_intents
        ]
        if not generic_intents:
            return result
        scoped_request = request.model_copy(update={"intents": generic_intents})
        return await self.delegate.annotate(scoped_request, result)

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)
