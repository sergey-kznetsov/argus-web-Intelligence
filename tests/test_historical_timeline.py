from __future__ import annotations

from argus.contracts.models import CollectionRequest, Observation
from argus.history.timeline import HistoricalTimelineBuilder


ORIGINAL_URL = "https://example.com/project"
CAPTURE_1 = "20240101000000"
CAPTURE_2 = "20250101000000"


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="historical-test",
        analysis_id="historical-analysis",
        territory={"city": "Ижевск"},
        intents=["historical_context"],
    )


def page(timestamp: str, text: str, content_hash: str) -> Observation:
    return Observation(
        observation_id=f"page-{timestamp}",
        collection_id="historical-collection",
        analysis_id="historical-analysis",
        consumer="historical-test",
        source="generic_web",
        source_kind="web_page",
        url=f"https://web.archive.org/web/{timestamp}id_/{ORIGINAL_URL}",
        entity_type="document",
        title="Project page",
        text=text,
        content_hash=content_hash,
    )


def entity(
    timestamp: str,
    *,
    entity_id: str,
    name: str,
    operator: str | None = None,
    brand: str | None = None,
) -> Observation:
    data = {"name": name}
    if operator is not None:
        data["operator"] = operator
    if brand is not None:
        data["brand"] = brand
    return Observation(
        observation_id=f"entity-{entity_id}-{timestamp}",
        collection_id="historical-collection",
        analysis_id="historical-analysis",
        consumer="historical-test",
        source="generic_web",
        source_kind="json_ld",
        url=f"https://web.archive.org/web/{timestamp}id_/{ORIGINAL_URL}",
        entity_type="organization",
        entity_id=entity_id,
        title=name,
        data=data,
        content_hash=(timestamp[-8:] + entity_id).encode().hex()[:64].ljust(64, "0"),
    )


def derive(builder, current, previous, current_timestamp=CAPTURE_2, previous_timestamp=CAPTURE_1):
    return builder.derive(
        current=current,
        previous=previous,
        request=request(),
        original_url=ORIGINAL_URL,
        capture_url=f"https://web.archive.org/web/{current_timestamp}id_/{ORIGINAL_URL}",
        capture_timestamp=current_timestamp,
        previous_capture_timestamp=previous_timestamp if previous else None,
    )


def test_first_capture_is_first_observed_not_inferred_appearance():
    builder = HistoricalTimelineBuilder()
    result = derive(
        builder,
        [page(CAPTURE_1, "first version", "a" * 64)],
        [],
        current_timestamp=CAPTURE_1,
        previous_timestamp=None,
    )

    assert len(result.observations) == 1
    item = result.observations[0]
    assert item.source_kind == "historical_page_version"
    assert item.data["change_type"] == "first_observed_capture"
    assert item.data["previous_capture_timestamp"] is None
    assert item.provenance["historical"]["semantic_inference"] is False


def test_page_change_records_hashes_timestamps_and_bounded_diff():
    builder = HistoricalTimelineBuilder(max_diff_chars=1_000)
    previous = page(CAPTURE_1, "name: Old\noperator: Alpha", "a" * 64)
    current = page(CAPTURE_2, "name: New\noperator: Beta", "b" * 64)

    result = derive(builder, [current], [previous])

    version = result.observations[0]
    assert version.data["change_type"] == "page_content_changed"
    assert version.data["previous_capture_timestamp"] == CAPTURE_1
    assert version.data["capture_timestamp"] == CAPTURE_2
    assert version.data["previous_content_hash"] == "a" * 64
    assert version.data["current_content_hash"] == "b" * 64
    assert "Old" in version.data["diff"]
    assert "New" in version.data["diff"]
    assert len(version.data["diff"]) <= 1_000


def test_entity_name_operator_and_brand_changes_are_explicit_from_to_values():
    builder = HistoricalTimelineBuilder()
    previous_page = page(CAPTURE_1, "old", "a" * 64)
    current_page = page(CAPTURE_2, "new", "b" * 64)
    previous_entity = entity(
        CAPTURE_1,
        entity_id="org-1",
        name="Old Name",
        operator="Operator A",
        brand="Brand A",
    )
    current_entity = entity(
        CAPTURE_2,
        entity_id="org-1",
        name="New Name",
        operator="Operator B",
        brand="Brand B",
    )

    result = derive(
        builder,
        [current_page, current_entity],
        [previous_page, previous_entity],
    )

    changes = [
        item for item in result.observations if item.source_kind == "historical_entity_change"
    ]
    assert len(changes) == 1
    change = changes[0]
    assert change.data["change_type"] == "fields_changed"
    fields = change.data["field_changes"]
    assert fields["name"] == {"from": "Old Name", "to": "New Name"}
    assert fields["operator"] == {"from": "Operator A", "to": "Operator B"}
    assert fields["brand"] == {"from": "Brand A", "to": "Brand B"}
    assert change.quality["semantic_inference"] is False


def test_appeared_and_disappeared_are_only_between_two_observed_captures():
    builder = HistoricalTimelineBuilder()
    previous = [
        page(CAPTURE_1, "old", "a" * 64),
        entity(CAPTURE_1, entity_id="gone", name="Gone Co"),
    ]
    current = [
        page(CAPTURE_2, "new", "b" * 64),
        entity(CAPTURE_2, entity_id="new", name="New Co"),
    ]

    result = derive(builder, current, previous)
    types = {
        item.data["change_type"]
        for item in result.observations
        if item.source_kind == "historical_entity_change"
    }

    assert types == {"appeared_between_captures", "disappeared_between_captures"}


def test_entity_change_budget_is_bounded_and_reported():
    builder = HistoricalTimelineBuilder(max_entity_changes=2)
    previous = [page(CAPTURE_1, "old", "a" * 64)] + [
        entity(CAPTURE_1, entity_id=f"old-{index}", name=f"Old {index}")
        for index in range(3)
    ]
    current = [page(CAPTURE_2, "new", "b" * 64)] + [
        entity(CAPTURE_2, entity_id=f"new-{index}", name=f"New {index}")
        for index in range(3)
    ]

    result = derive(builder, current, previous)

    derived_changes = [
        item for item in result.observations if item.source_kind == "historical_entity_change"
    ]
    assert result.changes_seen == 6
    assert result.truncated is True
    assert len(derived_changes) == 2
