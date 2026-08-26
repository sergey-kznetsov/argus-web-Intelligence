from __future__ import annotations

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest, Observation
from argus.research.coverage import IntentCoverageEvaluator
from argus.research.intent_evidence import (
    IntentEvidenceFinding,
    OllamaIntentEvidenceClassifier,
)
from argus.sources.base import SourceResult


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {
            "response": (
                '{"findings":['
                '{"intent":"complaints","excerpt":"Жители жалуются на постоянный шум ночью."},'
                '{"intent":"incidents","excerpt":"Вчера в доме произошел пожар, людей эвакуировали."},'
                '{"intent":"incidents","excerpt":"Придуманный взрыв, которого в тексте нет."}'
                "]}"
            )
        }


class FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    async def post(self, url: str, json: dict[str, object]):
        assert url.endswith("/api/generate")
        assert json["format"] == "json"
        return FakeResponse()


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="intent-test",
        analysis_id="analysis-intent-test",
        territory={"city": "Ижевск"},
        intents=["complaints", "incidents"],
    )


def page_observation() -> Observation:
    text = (
        "Ижевск. Жители жалуются на постоянный шум ночью. "
        "Вчера в доме произошел пожар, людей эвакуировали. "
        "Других происшествий не отмечено."
    )
    return Observation(
        observation_id="obs-intent-1",
        collection_id="collection-intent-1",
        analysis_id="analysis-intent-test",
        consumer="intent-test",
        source="generic_web",
        source_kind="web_page",
        url="https://example.com/news",
        entity_type="document",
        text=text,
        content_hash="a" * 64,
    )


def perm_request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="kraken-simulation",
        analysis_id="perm-quality",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=list(intents),
    )


def observation(*, text: str, url: str = "https://example.com/page") -> Observation:
    return Observation(
        observation_id="obs-perm",
        collection_id="collection-perm",
        analysis_id="perm-quality",
        consumer="kraken-simulation",
        source="generic_web",
        source_kind="web_page",
        url=url,
        entity_type="document",
        text=text,
        content_hash="b" * 64,
    )


@pytest.mark.asyncio
async def test_classifier_accepts_only_verified_exact_source_excerpts(monkeypatch):
    from argus.research import intent_evidence

    monkeypatch.setattr(intent_evidence.httpx, "AsyncClient", FakeClient)
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    item = page_observation()
    result = SourceResult(observations=[item])

    annotated = await classifier.annotate(request(), result)

    assert item.quality["intent_evidence"] == {
        "complaints": True,
        "incidents": True,
    }
    assert item.quality["territory_relevant"] is True
    semantic = [entry for entry in annotated.evidence if entry.type == "semantic_intent_excerpt"]
    assert len(semantic) == 2
    assert {entry.metadata["intent"] for entry in semantic} == {"complaints", "incidents"}
    assert all(entry.metadata["exact_source_excerpt_verified"] is True for entry in semantic)
    assert all(entry.metadata["territory_relevance_verified"] is True for entry in semantic)
    assert all(entry.metadata["model_output_is_evidence"] is False for entry in semantic)
    assert all(entry.text in (item.text or "") for entry in semantic)

    counts = IntentCoverageEvaluator().counts([item])
    assert counts["complaints"] == 1
    assert counts["incidents"] == 1


@pytest.mark.asyncio
async def test_unrelated_address_is_rejected_before_llm(monkeypatch):
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    item = observation(
        text=(
            "Пермь. На улице Ленина, 58 жители жалуются на шум, "
            "а вечером в доме произошел пожар."
        )
    )

    async def fail_if_called(*args, **kwargs):
        del args, kwargs
        raise AssertionError("semantic LLM must not run for territorially irrelevant pages")

    monkeypatch.setattr(classifier, "_findings", fail_if_called)
    result = await classifier.annotate(perm_request("complaints", "incidents"), SourceResult(observations=[item]))

    assert result.evidence == []
    assert item.quality["territory_relevant"] is False
    assert "intent_evidence" not in item.quality
    assert item.provenance["territory_relevance"]["basis"] == "address_anchor_missing"


@pytest.mark.asyncio
async def test_local_news_cannot_become_review_from_model_label(monkeypatch):
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    excerpt = "На Комсомольском проспекте, 27 завершили ремонт фасада."
    item = observation(text=f"Пермь. {excerpt}")

    async def findings(*args, **kwargs):
        del args, kwargs
        return [IntentEvidenceFinding("reviews", excerpt, "semantic_exact_excerpt")]

    monkeypatch.setattr(classifier, "_findings", findings)
    result = await classifier.annotate(perm_request("reviews"), SourceResult(observations=[item]))

    assert result.evidence == []
    assert item.quality["territory_relevant"] is True
    assert "intent_evidence" not in item.quality
    assert item.provenance["intent_evidence_rejections"] == [
        {
            "classifier_version": classifier.version,
            "intent": "reviews",
            "reason": "source_shape_not_supported",
            "model_output_is_evidence": False,
        }
    ]


@pytest.mark.asyncio
async def test_public_map_plain_text_review_can_be_semantic_evidence(monkeypatch):
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    excerpt = "Удобное расположение, но ночью бывает шумно."
    item = observation(
        text=f"Пермь, Комсомольский проспект, 27. Отзывы посетителей. {excerpt}",
        url="https://yandex.ru/maps/org/prikamye/123456789/reviews/",
    )

    async def findings(*args, **kwargs):
        del args, kwargs
        return [IntentEvidenceFinding("reviews", excerpt, "semantic_exact_excerpt")]

    monkeypatch.setattr(classifier, "_findings", findings)
    result = await classifier.annotate(perm_request("reviews"), SourceResult(observations=[item]))

    assert item.quality["intent_evidence"]["reviews"] is True
    semantic = [entry for entry in result.evidence if entry.type == "semantic_intent_excerpt"]
    assert len(semantic) == 1
    assert semantic[0].text == excerpt
    assert semantic[0].metadata["territory_relevance_basis"] == "exact_address"


def test_classifier_rejects_exact_but_semantically_unmarked_excerpt():
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    text = "Сегодня во дворе открыли новую детскую площадку."

    findings = classifier._validate_findings(
        {
            "findings": [
                {
                    "intent": "incidents",
                    "excerpt": text,
                }
            ]
        },
        text,
        ["incidents"],
    )

    assert findings == []


def test_classifier_rejects_model_invented_excerpt():
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    text = "Жители жалуются на шум."

    findings = classifier._validate_findings(
        {
            "findings": [
                {
                    "intent": "complaints",
                    "excerpt": "Жители жалуются на протечки в подвале.",
                }
            ]
        },
        text,
        ["complaints"],
    )

    assert findings == []


def test_classifier_accepts_custom_module_intent_only_with_exact_source_excerpt():
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    text = "На парковке предусмотрено 120 машино-мест, въезд доступен для посетителей."

    findings = classifier._validate_findings(
        {
            "findings": [
                {
                    "intent": "parking_capacity",
                    "excerpt": "На парковке предусмотрено 120 машино-мест",
                },
                {
                    "intent": "parking_access",
                    "excerpt": "въезд доступен только сотрудникам",
                },
            ]
        },
        text,
        ["parking_capacity", "parking_access"],
    )

    assert [(item.intent, item.excerpt) for item in findings] == [
        ("parking_capacity", "На парковке предусмотрено 120 машино-мест")
    ]
    assert findings[0].marker == "semantic_exact_excerpt"


def test_classifier_limits_and_deduplicates_custom_requested_intents():
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    requested = classifier._requested_intents(
        ["Parking_Capacity", " parking_capacity ", *[f"custom_{index}" for index in range(20)]]
    )

    assert requested[0] == "parking_capacity"
    assert len(requested) == classifier.max_requested_intents
    assert len(set(requested)) == len(requested)
