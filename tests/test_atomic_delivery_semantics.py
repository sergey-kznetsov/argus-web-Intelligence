from __future__ import annotations

from argus.contracts.models import Observation
from argus.sources.atomic_content_web import AtomicContentWebAdapter
from argus.sources.base import SourceResult


def _observation(*, entity_type: str, source_kind: str, suffix: str) -> Observation:
    return Observation(
        observation_id=f"obs-{suffix}",
        collection_id="collection-1",
        analysis_id="analysis-1",
        consumer="kraken.development.uds",
        source="generic_web",
        source_kind=source_kind,
        url=f"https://example.org/{suffix}",
        entity_type=entity_type,
        text="Содержательный source-backed текст наблюдения для проверки границы доставки.",
        content_hash=f"hash-{suffix}",
    )


def test_navigation_surface_marks_whole_page_and_atomic_summaries_non_messageable():
    document = _observation(entity_type="document", source_kind="web_page", suffix="listing")
    summary = _observation(
        entity_type="publication",
        source_kind="microformat_h_entry",
        suffix="summary",
    )
    technical = _observation(
        entity_type="structured_entity",
        source_kind="json_ld",
        suffix="technical",
    )
    result = SourceResult(observations=[document, summary, technical])

    AtomicContentWebAdapter._mark_navigation_only(
        result,
        reason="linked_article_listing_cards",
    )

    assert document.quality["navigation_only"] is True
    assert document.quality["message_candidate"] is False
    assert summary.quality["navigation_only"] is True
    assert summary.quality["message_candidate"] is False
    assert "navigation_only" not in technical.quality
    assert document.provenance["content_navigation"]["classification_is_evidence"] is False


def test_atomic_entity_supersedes_only_whole_page_document_container():
    document = _observation(entity_type="document", source_kind="web_page", suffix="page")
    publication = _observation(
        entity_type="publication",
        source_kind="html_atomic",
        suffix="publication",
    )
    result = SourceResult(observations=[document, publication])

    AtomicContentWebAdapter._mark_document_container_superseded(
        result,
        reason="html_atomic_content",
    )

    assert document.quality["atomic_container_superseded"] is True
    assert document.quality["message_candidate"] is False
    assert "atomic_container_superseded" not in publication.quality
    assert publication.quality.get("message_candidate") is not False
    assert document.provenance["atomic_delivery"]["whole_page_container_only"] is True
