from pathlib import Path

from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.config import Settings
from argus.security.request_limits import RequestSizeLimitMiddleware


def auth_headers(settings: Settings) -> dict[str, str]:
    token = settings.token_file.read_text(encoding="utf-8").strip()
    return {"Authorization": f"Bearer {token}"}


def test_api_rejects_oversized_content_length(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "argus.sqlite",
        token_file=tmp_path / "token",
        api_max_request_bytes=4096,
        browser_serp_enabled=False,
    )
    payload = {
        "consumer": "request-limit-test",
        "analysis_id": "large-request",
        "territory": {
            "city": "Ижевск",
            "metadata": {"blob": "x" * 5000},
        },
        "intents": ["public_mentions"],
    }
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/collections",
            json=payload,
            headers=auth_headers(settings),
        )
    assert response.status_code == 413
    assert response.json()["detail"] == {
        "code": "REQUEST_BODY_TOO_LARGE",
        "max_bytes": 4096,
    }


async def test_streamed_body_is_limited_without_content_length():
    received_messages = [
        {"type": "http.request", "body": b"a" * 6, "more_body": True},
        {"type": "http.request", "body": b"b" * 6, "more_body": False},
    ]
    sent: list[dict[str, object]] = []

    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return received_messages.pop(0)

    async def send(message):
        sent.append(message)

    middleware = RequestSizeLimitMiddleware(downstream, max_bytes=10)
    await middleware(
        {"type": "http", "headers": []},
        receive,
        send,
    )

    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 413
