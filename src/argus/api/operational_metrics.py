from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from argus.config import Settings
from argus.human_interaction import (
    CaptchaAnswerSubmission,
    CaptchaChallengeNotFoundError,
    CaptchaChallengeStateError,
    FileCaptchaBroker,
)
from argus.research.lane_coverage import build_research_lane_coverage
from argus.security.http_hardening import apply_http_hardening
from argus.services import ServiceContainer


def register_operational_metrics_endpoint(
    app: FastAPI,
    *,
    settings: Settings,
    services: ServiceContainer,
    repository: Any,
    require_bearer: Callable[..., Any],
) -> None:
    """Register startup-time internal API extensions used by `create_app`."""

    apply_http_hardening(app, settings)
    captcha_broker = FileCaptchaBroker(settings.db_path.parent / "human_interaction")

    @app.get("/v1/operations/metrics", dependencies=[Depends(require_bearer)])
    async def operational_metrics() -> dict[str, object]:
        queue_payload: dict[str, object] | None = None
        queue_reader = getattr(repository, "queue_metrics", None)
        if settings.execution_role == "api" and callable(queue_reader):
            started = time.perf_counter()
            try:
                queue = await queue_reader(
                    worker_max_age_seconds=settings.worker_health_max_age_seconds
                )
            except Exception:
                services.metrics.inc("operations_queue_reads_total", status="error")
                queue_payload = {"status": "error"}
            else:
                services.metrics.inc("operations_queue_reads_total", status="ok")
                queue_payload = queue.as_dict()
            finally:
                services.metrics.observe(
                    "db_operation_duration_seconds",
                    time.perf_counter() - started,
                    operation="queue_metrics",
                )

        return {
            "process": services.metrics.snapshot(),
            "queue": queue_payload,
            "execution_role": settings.execution_role,
            "storage_backend": settings.storage_backend,
            "security": {
                "security_headers": True,
                "direct_peer_rate_limit": True,
                "forwarded_client_ip_trusted": False,
                "loopback_bind_enforced": True,
                "rate_limit_requests_per_minute": settings.api_rate_limit_requests_per_minute,
                "rate_limit_burst": settings.api_rate_limit_burst,
                "public_outbound_ports": settings.outbound_public_ports,
                "denied_outbound_host_count": len(settings.deny_outbound_hosts),
            },
            "human_interaction": {
                "captcha": {
                    "version": captcha_broker.version,
                    "manual_text_input": True,
                    "automatic_solving": False,
                    "pending": len(captcha_broker.list_pending()),
                }
            },
            "exporters": {
                "prometheus": False,
                "opentelemetry": False,
                "built_in_json": True,
            },
        }

    @app.get(
        "/v1/operations/research-coverage/{collection_id}",
        dependencies=[Depends(require_bearer)],
    )
    async def research_coverage(collection_id: str) -> dict[str, object]:
        """Return factual 7+3 serial-lane diagnostics for one collection."""

        started = time.perf_counter()
        record = await repository.get_collection(collection_id)
        if record is None:
            raise HTTPException(status_code=404, detail="collection not found")
        observations = await repository.list_observations(collection_id)
        evidence = await repository.list_evidence(collection_id)
        services.metrics.observe(
            "db_operation_duration_seconds",
            time.perf_counter() - started,
            operation="research_lane_coverage",
        )
        services.metrics.inc("operations_research_coverage_reads_total", status="ok")
        return build_research_lane_coverage(record, observations, evidence)

    @app.get(
        "/v1/operations/captcha",
        dependencies=[Depends(require_bearer)],
    )
    async def pending_captcha_challenges() -> dict[str, object]:
        """List live manual CAPTCHA challenges without exposing submitted answers."""

        challenges = captcha_broker.list_pending()
        return {
            "version": captcha_broker.version,
            "automatic_solving": False,
            "items": challenges,
            "count": len(challenges),
        }

    @app.get(
        "/v1/operations/captcha/{challenge_id}/screenshot",
        dependencies=[Depends(require_bearer)],
    )
    async def captcha_screenshot(challenge_id: str):
        try:
            path = captcha_broker.screenshot_path(challenge_id)
        except CaptchaChallengeNotFoundError as exc:
            raise HTTPException(status_code=404, detail="CAPTCHA challenge not found") from exc
        return FileResponse(path=path, media_type="image/png")

    @app.post(
        "/v1/operations/captcha/{challenge_id}/answer",
        dependencies=[Depends(require_bearer)],
    )
    async def submit_captcha_answer(
        challenge_id: str,
        submission: CaptchaAnswerSubmission,
    ) -> dict[str, object]:
        try:
            challenge = captcha_broker.submit_answer(challenge_id, submission.answer)
        except CaptchaChallengeNotFoundError as exc:
            raise HTTPException(status_code=404, detail="CAPTCHA challenge not found") from exc
        except CaptchaChallengeStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        services.metrics.inc("captcha_manual_answers_total", status="submitted")
        return challenge
