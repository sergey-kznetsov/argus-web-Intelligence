from __future__ import annotations

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest, Observation
from argus.research.coverage import IntentCoverageEvaluator
from argus.research.intent_evidence import OllamaIntentEvidenceClassifier
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
                ']}'
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
        "Жители жалуются на постоянный шум ночью. "
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


@pytest.mark.asyncio
async def test_classifier_accepts_only_verified_exact_source_excerpts(monkeypatch):
    from argus.research import intent_evidence

    monkeypatch.setattr(intent_evidence.httpx, "AsyncClient", FakeClient)
    classifier = OllamaIntentEvidenceClassifier(Settings(browser_serp_enabled=False))
    observation = page_observation()
    result = SourceResult(observations=[observation])

    annotated = await classifier.annotate(request(), result)

    assert observation.quality["intent_evidence"] == {
        "complaints": True,
        "incidents": True,
    }
    semantic = [item for item in annotated.evidence if item.type == "semantic_intent_excerpt"]
    assert len(semantic) == 2
    assert {item.metadata["intent"] for item in semantic} == {"complaints", "incidents"}
    assert all(item.metadata["exact_source_excerpt_verified"] is True for item in semantic)
    assert all(item.metadata["model_output_is_evidence"] is False for item in semantic)
    assert all(item.text in (observation.text or "") for item in semantic)

    counts = IntentCoverageEvaluator().counts([observation])
    assert counts["complaints"] == 1
    assert counts["incidents"] == 1


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
