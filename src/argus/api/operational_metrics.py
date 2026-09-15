from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse

from argus.config import Settings
from argus.human_interaction import (
    CaptchaAnswerSubmission,
    CaptchaChallengeNotFoundError,
    CaptchaChallengeStateError,
    CaptchaInteractionSubmission,
    FileCaptchaBroker,
)
from argus.research.lane_coverage import build_research_lane_coverage
from argus.security.http_hardening import apply_http_hardening
from argus.services import ServiceContainer


def _public_collection_interaction(payload: dict[str, object]) -> dict[str, object]:
    """Return the stable consumer-safe interaction contract.

    Collection consumers never receive the source URL, selector, screenshot path, answer,
    browser state, cookies, bearer material, or raw runtime errors. Raw broker state remains
    available only to the authenticated operator endpoints.
    """

    challenge_id = payload.get("challenge_id")
    error = payload.get("error")
    return {
        "version": payload.get("version"),
        "challenge_id": challenge_id,
        "interaction_id": payload.get("interaction_id") or challenge_id,
        "collection_id": payload.get("collection_id"),
        "analysis_id": payload.get("analysis_id"),
        "source_id": payload.get("source_id"),
        "kind": payload.get("kind"),
        "prompt": payload.get("prompt"),
        "status": payload.get("status"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "has_screenshot": bool(payload.get("has_screenshot")),
        "manual_input_supported": bool(payload.get("manual_input_supported")),
        "interactive_required": bool(payload.get("interactive_required")),
        "interactive_input_supported": bool(payload.get("interactive_input_supported")),
        "interaction_count": int(payload.get("interaction_count", 0) or 0),
        "error": (
            {
                "code": "INTERACTION_ERROR",
                "message": "Interaction processing requires operator attention",
            }
            if error
            else None
        ),
    }


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

    async def collection_or_404(collection_id: str):
        record = await repository.get_collection(collection_id)
        if record is None:
            raise HTTPException(status_code=404, detail="collection not found")
        return record

    def correlated_challenge_or_404(
        collection_id: str,
        analysis_id: str,
        challenge_id: str,
    ) -> dict[str, object]:
        try:
            payload = captcha_broker.assert_collection(challenge_id, collection_id)
        except CaptchaChallengeNotFoundError as exc:
            # Do not leak whether the identifier belongs to another collection.
            raise HTTPException(status_code=404, detail="interaction not found") from exc
        actual_analysis_id = str(payload.get("analysis_id") or "").strip()
        if not actual_analysis_id or actual_analysis_id != str(analysis_id).strip():
            # A collection identifier is not sufficient authority for another analysis.
            raise HTTPException(status_code=404, detail="interaction not found")
        return payload

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
                    "interactive_click_relay": True,
                    "refresh_relay": True,
                    "collection_scoped": True,
                    "arbitrary_browser_commands": False,
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
        """Operator view of all live challenges. Consumer modules must use collection routes."""

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

    @app.post(
        "/v1/operations/captcha/{challenge_id}/interaction",
        dependencies=[Depends(require_bearer)],
    )
    async def submit_captcha_interaction(
        challenge_id: str,
        submission: CaptchaInteractionSubmission,
    ) -> dict[str, object]:
        try:
            challenge = captcha_broker.submit_interaction(
                challenge_id,
                action=submission.action,
                x_ratio=submission.x_ratio,
                y_ratio=submission.y_ratio,
            )
        except CaptchaChallengeNotFoundError as exc:
            raise HTTPException(status_code=404, detail="CAPTCHA challenge not found") from exc
        except CaptchaChallengeStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        services.metrics.inc(
            "captcha_manual_interactions_total",
            status=submission.action,
        )
        return challenge

    @app.get(
        "/v1/collections/{collection_id}/interactions",
        dependencies=[Depends(require_bearer)],
    )
    async def collection_interactions(collection_id: str) -> dict[str, object]:
        """Expose only interactions correlated with this collection and its analysis."""

        record = await collection_or_404(collection_id)
        analysis_id = str(record.request.analysis_id)
        items = [
            _public_collection_interaction(item)
            for item in captcha_broker.list_pending_for_collection(collection_id)
            if str(item.get("analysis_id") or "").strip() == analysis_id
        ]
        return {
            "version": captcha_broker.version,
            "collection_id": collection_id,
            "analysis_id": analysis_id,
            "items": items,
            "count": len(items),
        }

    @app.get(
        "/v1/collections/{collection_id}/interactions/{challenge_id}/screenshot",
        dependencies=[Depends(require_bearer)],
    )
    async def collection_interaction_screenshot(collection_id: str, challenge_id: str):
        record = await collection_or_404(collection_id)
        correlated_challenge_or_404(collection_id, str(record.request.analysis_id), challenge_id)
        try:
            content = captcha_broker.screenshot_path(challenge_id).read_bytes()
        except (CaptchaChallengeNotFoundError, OSError) as exc:
            raise HTTPException(status_code=404, detail="interaction not found") from exc
        return Response(
            content=content,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/v1/collections/{collection_id}/interactions/{challenge_id}/answer",
        dependencies=[Depends(require_bearer)],
    )
    async def submit_collection_interaction_answer(
        collection_id: str,
        challenge_id: str,
        submission: CaptchaAnswerSubmission,
    ) -> dict[str, object]:
        record = await collection_or_404(collection_id)
        correlated_challenge_or_404(collection_id, str(record.request.analysis_id), challenge_id)
        try:
            challenge = captcha_broker.submit_answer(challenge_id, submission.answer)
        except CaptchaChallengeStateError as exc:
            raise HTTPException(status_code=409, detail="interaction is not accepting text input") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid interaction answer") from exc
        services.metrics.inc("captcha_collection_answers_total", status="submitted")
        return _public_collection_interaction(challenge)

    @app.post(
        "/v1/collections/{collection_id}/interactions/{challenge_id}/interaction",
        dependencies=[Depends(require_bearer)],
    )
    async def submit_collection_interaction_action(
        collection_id: str,
        challenge_id: str,
        submission: CaptchaInteractionSubmission,
    ) -> dict[str, object]:
        record = await collection_or_404(collection_id)
        correlated_challenge_or_404(collection_id, str(record.request.analysis_id), challenge_id)
        try:
            challenge = captcha_broker.submit_interaction(
                challenge_id,
                action=submission.action,
                x_ratio=submission.x_ratio,
                y_ratio=submission.y_ratio,
            )
        except CaptchaChallengeStateError as exc:
            raise HTTPException(status_code=409, detail="interaction is not accepting this action") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid interaction action") from exc
        services.metrics.inc(
            "captcha_collection_interactions_total",
            status=submission.action,
        )
        return _public_collection_interaction(challenge)
