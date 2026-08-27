from __future__ import annotations

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest, Observation
from argus.research.intent_evidence import (
    IntentEvidenceFinding,
    OllamaIntentEvidenceClassifier,
)
from argus.sources.base import SourceResult


def _request() -> CollectionRequest:
    return CollectionRequest(
        consumer="historical-test",
        analysis_id="historical-test",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["historical_context"],
    )


def _observation(text: str) -> Observation:
    return Observation(
        observation_id="historical-observation",
        collection_id="historical-collection",
        analysis_id="historical-test",
        consumer="historical-test",
        source="generic_web",
        source_kind="web_page",
        url="https://example.org/history",
        entity_type="document",
        text=text,
        content_hash="c" * 64,
    )


def test_historical_context_is_builtin_and_requires_source_marker():
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    assert "historical_context" in classifier.builtin_intents
    assert "historical_context" in classifier.marker_required_intents

    excerpt = "Дом по адресу Комсомольский проспект, 27 построен в 1953 году."
    findings = classifier._validate_findings(
        {"findings": [{"intent": "historical_context", "excerpt": excerpt}]},
        excerpt,
        ["historical_context"],
    )

    assert [(item.intent, item.excerpt, item.marker) for item in findings] == [
        ("historical_context", excerpt, "построен")
    ]


def test_historical_context_accepts_old_source_declared_year():
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    excerpt = "Комсомольский проспект, 27 в Перми: архивная запись датирована 1968 годом."

    findings = classifier._validate_findings(
        {"findings": [{"intent": "historical_context", "excerpt": excerpt}]},
        excerpt,
        ["historical_context"],
    )

    assert len(findings) == 1
    assert findings[0].marker == "historical_year:1968"


def test_historical_context_rejects_exact_excerpt_without_historical_marker():
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    excerpt = "Комсомольский проспект, 27 находится в центре Перми."

    findings = classifier._validate_findings(
        {"findings": [{"intent": "historical_context", "excerpt": excerpt}]},
        excerpt,
        ["historical_context"],
    )

    assert findings == []


@pytest.mark.asyncio
async def test_historical_context_still_requires_territory_relevance(monkeypatch):
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    item = _observation("Пермь, улица Ленина, 58. Дом построен в 1953 году.")

    async def fail_if_called(*args, **kwargs):
        del args, kwargs
        raise AssertionError("LLM must not classify historical facts for another address")

    monkeypatch.setattr(classifier, "_findings", fail_if_called)
    result = await classifier.annotate(_request(), SourceResult(observations=[item]))

    assert result.evidence == []
    assert item.quality["territory_relevant"] is False
    assert "intent_evidence" not in item.quality


@pytest.mark.asyncio
async def test_historical_context_evidence_is_exact_source_excerpt(monkeypatch):
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    excerpt = "Дом по адресу Комсомольский проспект, 27 построен в 1953 году."
    item = _observation(f"Пермь. {excerpt}")

    async def findings(*args, **kwargs):
        del args, kwargs
        return [IntentEvidenceFinding("historical_context", excerpt, "построен")]

    monkeypatch.setattr(classifier, "_findings", findings)
    result = await classifier.annotate(_request(), SourceResult(observations=[item]))

    assert item.quality["intent_evidence"]["historical_context"] is True
    semantic = [entry for entry in result.evidence if entry.type == "semantic_intent_excerpt"]
    assert len(semantic) == 1
    assert semantic[0].text == excerpt
    assert semantic[0].metadata["custom_intent"] is False
    assert semantic[0].metadata["exact_source_excerpt_verified"] is True
    assert semantic[0].metadata["model_output_is_evidence"] is False
