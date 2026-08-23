import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.config import Settings
from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    Evidence,
    EvidenceSource,
    Observation,
    utcnow,
)
from argus.pagination import (
    InvalidCursorError,
    decode_result_cursor,
    encode_result_cursor,
)
from argus.storage.sqlite import SQLiteRepository


def auth_headers(settings: Settings) -> dict[str, str]:
    token = settings.token_file.read_text(encoding="utf-8").strip()
    return {"Authorization": f"Bearer {token}"}


def make_record(collection_id: str, status: CollectionStatus) -> CollectionRecord:
    timestamp = utcnow()
    return CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="result-test",
            analysis_id=f"analysis-{collection_id}",
            territory={"city": "Ижевск"},
            intents=["public_mentions"],
        ),
        status=status,
        stage=status.value,
        created_at=timestamp,
        updated_at=timestamp,
    )


def make_observation(collection_id: str, index: int) -> Observation:
    return Observation(
        observation_id=f"obs-{index:03d}",
        collection_id=collection_id,
        analysis_id=f"analysis-{collection_id}",
        consumer="result-test",
        source="test",
        source_kind="document",
        url=f"https://example.com/{index}",
        entity_type="document",
        text=f"observation-{index}",
        content_hash=(f"{index:x}" * 64)[:64],
    )


def make_evidence(observation: Observation, index: int) -> Evidence:
    return Evidence(
        evidence_id=f"evidence-{index:03d}",
        observation_id=observation.observation_id,
        type="document",
        text=f"evidence-{index}",
        source=EvidenceSource(
            provider="test",
            url=observation.url,
            collected_at=utcnow(),
            source_id="test",
        ),
    )


async def seed_result_database(path: Path, collection_id: str, *, terminal: bool = True) -> None:
    repository = SQLiteRepository(path)
    await repository.initialize()
    record = make_record(
        collection_id,
        CollectionStatus.COMPLETED if terminal else CollectionStatus.RUNNING,
    )
    await repository.create_collection(record)
    for index in range(3):
        observation = make_observation(collection_id, index)
        await repository.add_observation(observation)
        await repository.add_evidence(make_evidence(observation, index), collection_id)
    await repository.close()


def test_result_cursor_is_bound_to_collection_and_kind():
    cursor = encode_result_cursor("collection-a", "observation", "obs-001")
    decoded = decode_result_cursor(
        cursor,
        collection_id="collection-a",
        kind="observation",
    )
    assert decoded.item_id == "obs-001"

    with pytest.raises(InvalidCursorError):
        decode_result_cursor(cursor, collection_id="collection-b", kind="observation")
    with pytest.raises(InvalidCursorError):
        decode_result_cursor(cursor, collection_id="collection-a", kind="evidence")


def test_large_result_requires_pagination_and_pages_are_stable(tmp_path: Path):
    db_path = tmp_path / "argus.sqlite"
    collection_id = "large-result"
    asyncio.run(seed_result_database(db_path, collection_id))
    settings = Settings(
        db_path=db_path,
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
        api_full_result_max_items=4,
        api_full_result_max_bytes=1024 * 1024,
        api_result_page_default_size=1,
        api_result_page_max_size=2,
    )

    with TestClient(create_app(settings)) as client:
        headers = auth_headers(settings)
        full = client.get(f"/v1/collections/{collection_id}/result", headers=headers)
        assert full.status_code == 409
        detail = full.json()["detail"]
        assert detail["code"] == "RESULT_REQUIRES_PAGINATION"
        assert detail["observation_count"] == 3
        assert detail["evidence_count"] == 3

        summary = client.get(
            f"/v1/collections/{collection_id}/result/summary",
            headers=headers,
        )
        assert summary.status_code == 200
        summary_payload = summary.json()
        assert summary_payload["full_result_available"] is False
        assert summary_payload["observation_count"] == 3
        assert summary_payload["evidence_count"] == 3
        assert summary_payload["delivery_limits"]["page_max_size"] == 2

        first = client.get(
            f"/v1/collections/{collection_id}/result/observations?limit=1",
            headers=headers,
        )
        assert first.status_code == 200
        first_payload = first.json()
        assert [item["observation_id"] for item in first_payload["items"]] == ["obs-000"]
        assert first_payload["total_count"] == 3
        assert first_payload["next_cursor"]

        second = client.get(
            f"/v1/collections/{collection_id}/result/observations",
            params={"limit": 1, "cursor": first_payload["next_cursor"]},
            headers=headers,
        )
        assert second.status_code == 200
        second_payload = second.json()
        assert [item["observation_id"] for item in second_payload["items"]] == ["obs-001"]

        wrong_kind = client.get(
            f"/v1/collections/{collection_id}/result/evidence",
            params={"cursor": first_payload["next_cursor"]},
            headers=headers,
        )
        assert wrong_kind.status_code == 400


def test_small_result_keeps_legacy_full_result_contract(tmp_path: Path):
    db_path = tmp_path / "argus.sqlite"
    collection_id = "small-result"
    asyncio.run(seed_result_database(db_path, collection_id))
    settings = Settings(
        db_path=db_path,
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
        api_full_result_max_items=10,
        api_full_result_max_bytes=1024 * 1024,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/v1/collections/{collection_id}/result",
            headers=auth_headers(settings),
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["observations"]) == 3
        assert len(payload["evidence"]) == 3
        assert payload["status"] == "completed"


def test_paged_result_requires_terminal_collection(tmp_path: Path):
    db_path = tmp_path / "argus.sqlite"
    collection_id = "running-result"
    asyncio.run(seed_result_database(db_path, collection_id, terminal=False))
    settings = Settings(
        db_path=db_path,
        token_file=tmp_path / "token",
        browser_serp_enabled=False,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/v1/collections/{collection_id}/result/observations",
            headers=auth_headers(settings),
        )
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "RESULT_NOT_FINAL",
            "status": "running",
        }
