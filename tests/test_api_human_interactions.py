from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.config import Settings
from argus.contracts.models import CollectionRecord, CollectionRequest, CollectionStatus, utcnow
from argus.human_interaction import FileCaptchaBroker


def _headers(settings: Settings) -> dict[str, str]:
    token = settings.token_file.read_text(encoding="utf-8").strip()
    return {"Authorization": f"Bearer {token}"}


def _broker(settings: Settings) -> FileCaptchaBroker:
    return FileCaptchaBroker(settings.db_path.parent / "human_interaction")


def _persist_collection(
    client: TestClient,
    *,
    collection_id: str,
    analysis_id: str,
) -> None:
    timestamp = utcnow()
    record = CollectionRecord(
        collection_id=collection_id,
        request=CollectionRequest(
            consumer="interaction-test",
            analysis_id=analysis_id,
            territory={"city": "Ижевск"},
            intents=["interaction_fixture"],
        ),
        status=CollectionStatus.QUEUED,
        stage="queued",
        created_at=timestamp,
        updated_at=timestamp,
    )
    assert client.portal is not None
    client.portal.call(client.app.state.repository.create_collection, record)


def test_collection_interaction_http_relay_is_scoped_and_secret_safe(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "db.sqlite", token_file=tmp_path / "token")
    with TestClient(create_app(settings)) as client:
        _persist_collection(client, collection_id="collection-a", analysis_id="analysis-a")
        _persist_collection(client, collection_id="collection-b", analysis_id="analysis-b")
        broker = _broker(settings)
        text = broker.create(
            url="https://dom.mingkh.ru/izhevsk/house/1?session=private",
            screenshot=b"\x89PNG\r\n\x1a\ntext",
            kind="text",
            prompt="Введите символы CAPTCHA.",
            input_selector="input[name=captcha-secret]",
            collection_id="collection-a",
            analysis_id="analysis-a",
            source_id="mingkh_residential",
        )
        interactive = broker.create(
            url="https://dom.mingkh.ru/izhevsk/house/1",
            screenshot=b"\x89PNG\r\n\x1a\ninteractive",
            kind="interactive",
            prompt="Кликните по проверке.",
            input_selector=None,
            collection_id="collection-a",
            analysis_id="analysis-a",
            source_id="mingkh_residential",
        )
        other_collection = broker.create(
            url="https://dom.mingkh.ru/izhevsk/house/2",
            screenshot=b"\x89PNG\r\n\x1a\nother",
            kind="text",
            prompt="CAPTCHA.",
            input_selector="input[name=captcha]",
            collection_id="collection-b",
            analysis_id="analysis-b",
            source_id="mingkh_residential",
        )
        wrong_analysis = broker.create(
            url="https://dom.mingkh.ru/izhevsk/house/3",
            screenshot=b"\x89PNG\r\n\x1a\nwrong-analysis",
            kind="interactive",
            prompt="CAPTCHA.",
            input_selector=None,
            collection_id="collection-a",
            analysis_id="analysis-b",
            source_id="mingkh_residential",
        )
        interactive_id = str(interactive["challenge_id"])
        wrong_analysis_id = str(wrong_analysis["challenge_id"])
        broker.mark_interactive_required(
            interactive_id,
            error="Bearer must-not-leak C:/private/runtime/cookies.json",
        )
        broker.mark_interactive_required(wrong_analysis_id)

        assert client.get("/v1/collections/collection-a/interactions").status_code == 401
        response = client.get(
            "/v1/collections/collection-a/interactions",
            headers=_headers(settings),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["analysis_id"] == "analysis-a"
        assert payload["count"] == 2
        assert {item["challenge_id"] for item in payload["items"]} == {
            text["challenge_id"],
            interactive["challenge_id"],
        }
        assert all(item["collection_id"] == "collection-a" for item in payload["items"])
        assert all(item["analysis_id"] == "analysis-a" for item in payload["items"])
        assert all(item["source_id"] == "mingkh_residential" for item in payload["items"])
        serialized = response.text
        assert str(other_collection["challenge_id"]) not in serialized
        assert wrong_analysis_id not in serialized
        assert "captcha-secret" not in serialized
        assert "session=private" not in serialized
        assert "must-not-leak" not in serialized
        assert "C:/private" not in serialized
        assert "cookies.json" not in serialized
        assert "screenshot_file" not in serialized
        assert "input_selector" not in serialized
        assert "answer" not in serialized

        screenshot = client.get(
            f"/v1/collections/collection-a/interactions/{text['challenge_id']}/screenshot",
            headers=_headers(settings),
        )
        assert screenshot.status_code == 200
        assert screenshot.headers["content-type"].startswith("image/png")
        assert screenshot.headers["cache-control"] == "no-store"
        assert screenshot.content == b"\x89PNG\r\n\x1a\ntext"

        cross_collection = client.get(
            f"/v1/collections/collection-b/interactions/{text['challenge_id']}/screenshot",
            headers=_headers(settings),
        )
        assert cross_collection.status_code == 404
        assert cross_collection.json()["detail"] == "interaction not found"

        cross_analysis = client.post(
            f"/v1/collections/collection-a/interactions/{wrong_analysis_id}/interaction",
            headers=_headers(settings),
            json={"action": "refresh"},
        )
        assert cross_analysis.status_code == 404
        assert cross_analysis.json()["detail"] == "interaction not found"

        answered = client.post(
            f"/v1/collections/collection-a/interactions/{text['challenge_id']}/answer",
            headers=_headers(settings),
            json={"answer": "AB12"},
        )
        assert answered.status_code == 200
        assert answered.json()["status"] == "answered"
        assert "AB12" not in answered.text
        assert "session=private" not in answered.text
        assert broker.get(str(text["challenge_id"]))["answer"] == "AB12"

        clicked = client.post(
            f"/v1/collections/collection-a/interactions/{interactive_id}/interaction",
            headers=_headers(settings),
            json={"action": "click", "x_ratio": 0.25, "y_ratio": 0.75},
        )
        assert clicked.status_code == 200
        assert clicked.json()["status"] == "interaction_queued"
        assert "must-not-leak" not in clicked.text
        assert broker.get(interactive_id)["interaction"] == {
            "action": "click",
            "x_ratio": 0.25,
            "y_ratio": 0.75,
        }

        out_of_range = client.post(
            f"/v1/collections/collection-a/interactions/{interactive_id}/interaction",
            headers=_headers(settings),
            json={"action": "click", "x_ratio": 1.01, "y_ratio": 0.5},
        )
        assert out_of_range.status_code == 422


def test_refresh_relay_keeps_collection_and_analysis_scope(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "db.sqlite", token_file=tmp_path / "token")
    with TestClient(create_app(settings)) as client:
        _persist_collection(client, collection_id="collection-a", analysis_id="analysis-a")
        _persist_collection(client, collection_id="collection-b", analysis_id="analysis-b")
        broker = _broker(settings)
        challenge = broker.create(
            url="https://dom.mingkh.ru/izhevsk/house/1",
            screenshot=b"png",
            kind="interactive",
            prompt="Кликните по проверке.",
            input_selector=None,
            collection_id="collection-a",
            analysis_id="analysis-a",
            source_id="mingkh_residential",
        )
        challenge_id = str(challenge["challenge_id"])
        broker.mark_interactive_required(challenge_id)

        cross_collection = client.post(
            f"/v1/collections/collection-b/interactions/{challenge_id}/interaction",
            headers=_headers(settings),
            json={"action": "refresh"},
        )
        assert cross_collection.status_code == 404

        refreshed = client.post(
            f"/v1/collections/collection-a/interactions/{challenge_id}/interaction",
            headers=_headers(settings),
            json={"action": "refresh"},
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["collection_id"] == "collection-a"
        assert refreshed.json()["analysis_id"] == "analysis-a"
        assert refreshed.json()["interaction_id"] == challenge_id
        assert refreshed.json()["status"] == "interaction_queued"
        assert broker.get(challenge_id)["interaction"] == {"action": "refresh"}
