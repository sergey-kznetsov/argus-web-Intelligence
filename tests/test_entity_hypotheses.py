from __future__ import annotations

from argus.config import Settings
from argus.contracts.models import CollectionRequest, Observation
from argus.research.entity_hypotheses import OllamaEntityHypothesisExtractor


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="entity-hypothesis-test",
        analysis_id="entity-hypothesis-analysis",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["public_mentions", "historical_context"],
    )


def observation() -> Observation:
    return Observation(
        observation_id="observation-hypothesis",
        collection_id="collection-hypothesis",
        analysis_id="entity-hypothesis-analysis",
        consumer="entity-hypothesis-test",
        source="generic_web",
        source_kind="web_page",
        url="https://example.org/history",
        entity_type="document",
        title="История квартала",
        text=(
            "До реконструкции на этом месте работала гостиница «Прикамье». "
            "В архивной публикации также упоминается старое название улицы."
        ),
        content_hash="a" * 64,
    )


def test_entity_hypothesis_requires_exact_excerpt_and_label_inside_excerpt():
    extractor = OllamaEntityHypothesisExtractor(Settings(browser_serp_enabled=False))
    source = observation()
    text = source.text or ""

    values = extractor._validate(
        {
            "entities": [
                {
                    "type": "organization",
                    "label": "гостиница «Прикамье»",
                    "excerpt": "До реконструкции на этом месте работала гостиница «Прикамье».",
                },
                {
                    "type": "organization",
                    "label": "Несуществующий завод",
                    "excerpt": "Придуманный фрагмент источника.",
                },
                {
                    "type": "unsupported",
                    "label": "Прикамье",
                    "excerpt": "До реконструкции на этом месте работала гостиница «Прикамье».",
                },
            ]
        },
        text,
        source,
    )

    assert len(values) == 1
    item = values[0]
    assert item.entity_type == "organization"
    assert item.label == "гостиница «Прикамье»"
    assert item.excerpt in text
    assert item.is_evidence is False
    assert item.model_assisted is True


def test_entity_hypothesis_generates_bounded_navigation_query_not_fact():
    extractor = OllamaEntityHypothesisExtractor(Settings(browser_serp_enabled=False))
    source = observation()
    hypothesis = extractor._validate(
        {
            "entities": [
                {
                    "type": "organization",
                    "label": "гостиница «Прикамье»",
                    "excerpt": "До реконструкции на этом месте работала гостиница «Прикамье».",
                }
            ]
        },
        source.text or "",
        source,
    )[0]

    queries = extractor.query_hints(
        request(),
        [hypothesis],
        priority_intents=["historical_context", "public_mentions"],
        seen_queries=set(),
    )

    assert len(queries) == 1
    assert "гостиница «Прикамье»" in queries[0]
    assert "Комсомольский проспект, 27" in queries[0]
    assert "история" in queries[0]
    assert hypothesis.as_dict()["is_evidence"] is False


def test_entity_hypothesis_query_deduplicates_seen_navigation():
    extractor = OllamaEntityHypothesisExtractor(Settings(browser_serp_enabled=False))
    source = observation()
    hypothesis = extractor._validate(
        {
            "entities": [
                {
                    "type": "organization",
                    "label": "гостиница «Прикамье»",
                    "excerpt": "До реконструкции на этом месте работала гостиница «Прикамье».",
                }
            ]
        },
        source.text or "",
        source,
    )[0]
    first = extractor.query_hints(
        request(),
        [hypothesis],
        priority_intents=["historical_context"],
    )

    assert first
    second = extractor.query_hints(
        request(),
        [hypothesis],
        priority_intents=["historical_context"],
        seen_queries=set(first),
    )
    assert second == []
