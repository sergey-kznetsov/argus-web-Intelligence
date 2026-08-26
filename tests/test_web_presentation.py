from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from argus.contracts.models import (
    CollectionRecord,
    CollectionRequest,
    CollectionStatus,
    Evidence,
    EvidencePage,
    EvidenceSource,
    Observation,
    ObservationPage,
    utcnow,
)
from argus.web.app import create_web_app
from argus.web.config import WebSettings


class StubApiClient:
    def __init__(self) -> None:
        request = CollectionRequest(
            consumer="kraken.simulation",
            analysis_id="presentation-web",
            territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
            intents=["public_mentions"],
        )
        now = utcnow()
        self.record = CollectionRecord(
            collection_id="collection-presentation",
            request=request,
            status=CollectionStatus.COMPLETED,
            created_at=now,
            updated_at=now,
            progress_percent=100,
        )
        self.observation = Observation(
            observation_id="obs-1",
            collection_id=self.record.collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source="generic_web",
            source_kind="web_page",
            url="https://example.org/page",
            entity_type="document",
            title="Perm source",
            text="Public source about Perm.",
            content_hash="a" * 64,
        )
        self.evidence = Evidence(
            evidence_id="ev-1",
            observation_id=self.observation.observation_id,
            type="document",
            text="Public source about Perm.",
            source=EvidenceSource(
                provider="generic_web",
                url=self.observation.url,
                collected_at=now,
                source_id="generic_web",
            ),
        )

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def request_json(self, method, path, *, json_body=None, params=None):
        del method, json_body
        if path.endswith("/result/observations"):
            assert params == {"limit": 30}
            page = ObservationPage(
                collection_id=self.record.collection_id,
                status=CollectionStatus.COMPLETED,
                total_count=2,
                page_stored_bytes=100,
                items=[self.observation],
                next_cursor="more-observations",
            )
            return 200, page.model_dump(mode="json")
        if path.endswith("/result/evidence"):
            assert params == {"limit": 100}
            page = EvidencePage(
                collection_id=self.record.collection_id,
                status=CollectionStatus.COMPLETED,
                total_count=1,
                page_stored_bytes=100,
                items=[self.evidence],
                next_cursor=None,
            )
            return 200, page.model_dump(mode="json")
        if path.endswith(self.record.collection_id):
            return 200, self.record.model_dump(mode="json")
        return 404, {"detail": "not found"}


class StubPresenter:
    def __init__(self) -> None:
        self.calls = []

    async def build(self, request, observations, evidence, *, truncated=False):
        self.calls.append((request, observations, evidence, truncated))
        return {
            "language": "ru",
            "summary_ru": "Проверочная русская сводка.",
            "rows": [],
            "model_output_is_evidence": False,
            "truncated": truncated,
        }


def settings(tmp_path: Path) -> WebSettings:
    token_file = tmp_path / "token"
    token_file.write_text("api-secret\n", encoding="utf-8")
    password_file = tmp_path / "password"
    password_file.write_text("operator-secret\n", encoding="utf-8")
    return WebSettings(
        api_url="http://127.0.0.1:8787",
        api_token_file=token_file,
        password_file=password_file,
        username="argus",
    )


def test_web_presentation_is_authenticated_bounded_and_marks_truncation(tmp_path: Path):
    api = StubApiClient()
    presenter = StubPresenter()
    with TestClient(
        create_web_app(
            settings(tmp_path),
            api_client=api,
            presentation_service=presenter,
        )
    ) as client:
        denied = client.get("/api/collections/collection-presentation/presentation")
        assert denied.status_code == 401

        response = client.get(
            "/api/collections/collection-presentation/presentation",
            auth=("argus", "operator-secret"),
        )

    assert response.status_code == 200
    assert response.json()["language"] == "ru"
    assert response.json()["model_output_is_evidence"] is False
    assert response.json()["truncated"] is True
    assert len(presenter.calls) == 1
    request_value, observations, evidence, truncated = presenter.calls[0]
    assert request_value.constraints.output_language == "ru"
    assert [item.observation_id for item in observations] == ["obs-1"]
    assert [item.evidence_id for item in evidence] == ["ev-1"]
    assert truncated is True
