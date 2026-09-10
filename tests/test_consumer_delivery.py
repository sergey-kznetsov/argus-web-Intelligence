from __future__ import annotations

import pytest

from argus.consumer_delivery import ConsumerDeliveryProjector
from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation
from argus.toolpacks import resolved_tool_pack_from_request


class _Repository:
    def __init__(self, observations: list[Observation] | None = None) -> None:
        self.observations = list(observations or [])

    async def list_observations(self, collection_id: str) -> list[Observation]:
        return [
            item for item in self.observations if item.collection_id == collection_id
        ]


def _request(consumer: str = "kraken.development.uds") -> CollectionRequest:
    return CollectionRequest(
        consumer=consumer,
        analysis_id="consumer-delivery-test",
        territory={"city": "Ижевск", "address": "Пушкинская, 277"},
        intents=["complaints"],
    )


def _observation(
    observation_id: str,
    *,
    text: str,
    url: str,
    entity_type: str = "document",
    source_kind: str = "web_page",
) -> Observation:
    return Observation(
        observation_id=observation_id,
        collection_id="collection-1",
        analysis_id="consumer-delivery-test",
        consumer="kraken.development.uds",
        source="generic_web",
        source_kind=source_kind,
        url=url,
        entity_type=entity_type,
        text=text,
        content_hash=(observation_id[:1] or "a") * 64,
    )


def _evidence(observation_id: str, evidence_id: str, *, text: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        observation_id=observation_id,
        type="document",
        text=text,
        source=EvidenceSource(
            provider="generic_web",
            url="https://example.test/source",
            collected_at=_observation(
                "time-source",
                text="placeholder",
                url="https://example.test/time",
            ).collected_at,
            source_id="generic_web",
        ),
    )


@pytest.mark.asyncio
async def test_kraken_exact_duplicate_is_collapsed_and_evidence_is_preserved():
    text = (
        "Ижевск, Пушкинская. Жители пишут о проблеме у перехода и просят принять меры."
    )
    existing = _observation(
        "existing",
        text=text,
        url="https://example.test/a",
        entity_type="post",
        source_kind="json_ld",
    )
    duplicate = _observation(
        "duplicate",
        text=text,
        url="https://example.test/b",
        entity_type="document",
    )
    projector = ConsumerDeliveryProjector()
    pack = resolved_tool_pack_from_request(_request())
    assert pack is not None

    observations, evidence, stats = await projector.project_task_result(
        _Repository([existing]),
        collection_id="collection-1",
        pack=pack,
        observations=[duplicate],
        evidence=[_evidence("duplicate", "ev-duplicate", text=text)],
    )

    assert observations == []
    assert len(evidence) == 1
    assert evidence[0].observation_id == "existing"
    dedup = evidence[0].metadata["consumer_delivery_dedup"]
    assert dedup["duplicate_observation_id"] == "duplicate"
    assert dedup["canonical_observation_id"] == "existing"
    assert stats["duplicates_collapsed"] == 1
    assert stats["semantic_filtering_applied"] is False


@pytest.mark.asyncio
async def test_kraken_same_page_thin_document_wrapper_prefers_typed_post():
    post_text = (
        "Ижевск, улица Пушкинская, дом 277. Жители сообщают, что у перехода "
        "не работает освещение и вечером опасно переходить дорогу."
    )
    wrapper = _observation(
        "document",
        text=f"Жители сообщили о переходе. {post_text} 30 августа 2026.",
        url="https://example.test/thread/42",
    )
    post = _observation(
        "post",
        text=post_text,
        url="https://example.test/thread/42",
        entity_type="post",
        source_kind="json_ld",
    )
    projector = ConsumerDeliveryProjector()
    pack = resolved_tool_pack_from_request(_request())
    assert pack is not None

    observations, evidence, stats = await projector.project_task_result(
        _Repository(),
        collection_id="collection-1",
        pack=pack,
        observations=[wrapper, post],
        evidence=[
            _evidence("document", "ev-document", text=wrapper.text or ""),
            _evidence("post", "ev-post", text=post.text or ""),
        ],
    )

    assert [item.observation_id for item in observations] == ["post"]
    assert {item.observation_id for item in evidence} == {"post"}
    assert stats["duplicates_collapsed"] == 1


@pytest.mark.asyncio
async def test_kraken_public_map_observation_is_not_delivered_as_message_candidate():
    map_text = "Техническая оболочка карточки карты с рейтингом, координатами и служебными полями."
    map_observation = _observation(
        "map-shell",
        text=map_text,
        url="https://yandex.ru/maps/org/example/123/",
        entity_type="review",
        source_kind="public_map_web",
    ).model_copy(
        update={
            "quality": {
                "public_map_information_only": True,
                "message_candidate": False,
                "delivery_semantics": "evidence_context_only",
            },
            "provenance": {
                "public_map_delivery": {
                    "information_only": True,
                    "message_candidate": False,
                }
            },
        }
    )
    ordinary = _observation(
        "ordinary-post",
        text="Жители Пушкинской сообщают о сломанном освещении у пешеходного перехода.",
        url="https://example.test/post/1",
        entity_type="post",
        source_kind="json_ld",
    )
    map_evidence = _evidence("map-shell", "ev-map", text=map_text).model_copy(
        update={"metadata": {"public_map_information_only": True}}
    )
    ordinary_evidence = _evidence(
        "ordinary-post",
        "ev-post",
        text=ordinary.text or "",
    )
    projector = ConsumerDeliveryProjector()
    pack = resolved_tool_pack_from_request(_request())
    assert pack is not None

    observations, evidence, stats = await projector.project_task_result(
        _Repository(),
        collection_id="collection-1",
        pack=pack,
        observations=[map_observation, ordinary],
        evidence=[map_evidence, ordinary_evidence],
    )

    assert [item.observation_id for item in observations] == ["ordinary-post"]
    by_id = {item.evidence_id: item for item in evidence}
    assert set(by_id) == {"ev-map", "ev-post"}
    assert by_id["ev-map"].observation_id is None
    info_only = by_id["ev-map"].metadata["consumer_delivery_information_only"]
    assert info_only["context_observation_id"] == "map-shell"
    assert info_only["observation_delivered"] is False
    assert info_only["evidence_preserved"] is True
    assert by_id["ev-post"].observation_id == "ordinary-post"
    assert stats["information_only_observations_suppressed"] == 1
    assert stats["information_only_filtering_applied"] is True
    assert stats["semantic_filtering_applied"] is False


@pytest.mark.asyncio
async def test_public_map_provenance_marker_is_defensive_delivery_guard():
    map_observation = _observation(
        "map-provenance-only",
        text="Map shell payload that must remain evidence context only for Kraken.",
        url="https://www.google.com/maps/place/example",
    ).model_copy(
        update={
            "provenance": {
                "public_map_delivery": {
                    "information_only": True,
                }
            }
        }
    )
    projector = ConsumerDeliveryProjector()
    pack = resolved_tool_pack_from_request(_request())
    assert pack is not None

    observations, evidence, stats = await projector.project_task_result(
        _Repository(),
        collection_id="collection-1",
        pack=pack,
        observations=[map_observation],
        evidence=[],
    )

    assert observations == []
    assert evidence == []
    assert stats["information_only_observations_suppressed"] == 1


@pytest.mark.asyncio
async def test_other_consumer_keeps_default_delivery_semantics():
    observation = _observation(
        "generic",
        text="A sufficiently long generic observation that must not be projected away.",
        url="https://example.test/generic",
    )
    projector = ConsumerDeliveryProjector()
    pack = resolved_tool_pack_from_request(_request(consumer="test"))
    assert pack is not None

    observations, evidence, stats = await projector.project_task_result(
        _Repository(),
        collection_id="collection-1",
        pack=pack,
        observations=[observation],
        evidence=[],
    )

    assert observations == [observation]
    assert evidence == []
    assert stats["policy"] == "intent_evidence"
    assert stats["dedup_policy"] == "none"
    assert stats["duplicates_collapsed"] == 0
    assert stats["information_only_observations_suppressed"] == 0
    assert stats["information_only_filtering_applied"] is False
