from __future__ import annotations

from argus.config import Settings
from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation, utcnow
from argus.presentation import RussianPresentationService


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="presentation-test",
        analysis_id="presentation-analysis",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["public_mentions"],
    )


def observation(text: str = "The hotel is located in central Perm.") -> Observation:
    return Observation(
        observation_id="obs-presentation",
        collection_id="collection-presentation",
        analysis_id="presentation-analysis",
        consumer="presentation-test",
        source="generic_web",
        source_kind="web_page",
        url="https://example.org/page",
        entity_type="document",
        title="Hotel page",
        text=text,
        content_hash="a" * 64,
    )


def evidence() -> Evidence:
    return Evidence(
        evidence_id="ev-presentation",
        observation_id="obs-presentation",
        type="document",
        text="The hotel is located in central Perm.",
        source=EvidenceSource(
            provider="generic_web",
            url="https://example.org/page",
            collected_at=utcnow(),
            source_id="generic_web",
        ),
    )


def test_presentation_accepts_translation_only_with_exact_original_excerpt():
    service = RussianPresentationService(Settings(browser_serp_enabled=False))
    source = observation()
    payload = {
        "summary_ru": "Найдена публичная информация об объекте в Перми.",
        "rows": [
            {
                "observation_id": source.observation_id,
                "category_ru": "Публикация",
                "title_ru": "Страница гостиницы",
                "fact_ru": "Гостиница расположена в центральной части Перми.",
                "source_excerpt_original": "The hotel is located in central Perm.",
            }
        ],
    }

    rows = service._validated_rows(payload, [source], {source.observation_id: ["ev-presentation"]})

    assert len(rows) == 1
    row = rows[0]
    assert row.fact_ru == "Гостиница расположена в центральной части Перми."
    assert row.source_excerpt_original in (source.text or "")
    assert row.evidence_ids == ["ev-presentation"]
    assert service._validated_summary(payload).startswith("Найдена")


def test_presentation_rejects_invented_source_excerpt():
    service = RussianPresentationService(Settings(browser_serp_enabled=False))
    source = observation()

    rows = service._validated_rows(
        {
            "rows": [
                {
                    "observation_id": source.observation_id,
                    "category_ru": "Публикация",
                    "title_ru": "Страница гостиницы",
                    "fact_ru": "У гостиницы есть бассейн.",
                    "source_excerpt_original": "The hotel has a swimming pool.",
                }
            ]
        },
        [source],
        {},
    )

    assert rows == []


def test_presentation_deterministic_fallback_keeps_existing_russian_source_text():
    service = RussianPresentationService(Settings(browser_serp_enabled=False))
    source = observation("Гостиница расположена в центре Перми рядом с городской эспланадой.")

    rows = service._russian_source_fallback([source], {source.observation_id: ["ev-1"]})

    assert len(rows) == 1
    assert "Гостиница расположена" in rows[0].fact_ru
    assert rows[0].source_excerpt_original == rows[0].fact_ru
    assert rows[0].evidence_ids == ["ev-1"]


def test_presentation_fallback_does_not_label_english_source_as_russian():
    service = RussianPresentationService(Settings(browser_serp_enabled=False))

    rows = service._russian_source_fallback([observation()], {})

    assert rows == []


def test_presentation_metadata_explicitly_separates_model_output_from_evidence():
    service = RussianPresentationService(Settings(browser_serp_enabled=False))
    payload = service._empty(request(), truncated=True)

    assert payload["language"] == "ru"
    assert payload["model_output_is_evidence"] is False
    assert payload["source_language_preserved"] is True
    assert payload["original_evidence_available_separately"] is True
    assert payload["truncated"] is True
    assert payload["table_columns_ru"] == ["Категория", "Заголовок", "Факт", "Источник"]
