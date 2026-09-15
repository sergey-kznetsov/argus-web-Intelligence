from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response, status

from argus.config import Settings
from argus.human_interaction import (
    CaptchaAnswerSubmission,
    CaptchaChallengeNotFoundError,
    CaptchaChallengeStateError,
    CaptchaInteractionSubmission,
    FileCaptchaBroker,
)


def _public_error(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    return {
        "code": "INTERACTION_ERROR",
        "message": "Interaction processing requires operator attention",
    }


def _public_interaction(payload: dict[str, object]) -> dict[str, object]:
    """Return only the stable, operator-safe interaction contract."""

    challenge_id = payload.get("challenge_id")
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
        "error": _public_error(payload.get("error")),
    }


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="interaction not found",
    )


def _state_conflict(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="interaction is not accepting this action",
    )


def _invalid_action(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="invalid interaction action",
    )


def register_human_interaction_endpoints(
    app: FastAPI,
    *,
    settings: Settings,
    require_bearer: Callable[..., Any],
) -> None:
    """Expose the cross-process CAPTCHA mailbox through collection-scoped HTTP routes."""

    broker = FileCaptchaBroker(settings.db_path.parent / "human_interaction")
    dependencies = [Depends(require_bearer)]

    @app.get(
        "/v1/collections/{collection_id}/interactions",
        dependencies=dependencies,
    )
    async def collection_interactions(collection_id: str) -> dict[str, object]:
        items = [
            _public_interaction(item)
            for item in broker.list_pending_for_collection(collection_id)
        ]
        return {"items": items, "count": len(items)}

    @app.get(
        "/v1/collections/{collection_id}/interactions/{challenge_id}/screenshot",
        dependencies=dependencies,
    )
    async def collection_interaction_screenshot(
        collection_id: str,
        challenge_id: str,
    ) -> Response:
        try:
            broker.assert_collection(challenge_id, collection_id)
            content = broker.screenshot_path(challenge_id).read_bytes()
        except CaptchaChallengeNotFoundError as exc:
            raise _not_found(exc) from exc
        except OSError as exc:
            raise _not_found(exc) from exc
        return Response(
            content=content,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/v1/collections/{collection_id}/interactions/{challenge_id}/answer",
        dependencies=dependencies,
    )
    async def answer_collection_interaction(
        collection_id: str,
        challenge_id: str,
        submission: CaptchaAnswerSubmission,
    ) -> dict[str, object]:
        try:
            broker.assert_collection(challenge_id, collection_id)
            result = broker.submit_answer(challenge_id, submission.answer)
        except CaptchaChallengeNotFoundError as exc:
            raise _not_found(exc) from exc
        except CaptchaChallengeStateError as exc:
            raise _state_conflict(exc) from exc
        except ValueError as exc:
            raise _invalid_action(exc) from exc
        return _public_interaction(result)

    @app.post(
        "/v1/collections/{collection_id}/interactions/{challenge_id}/interaction",
        dependencies=dependencies,
    )
    async def act_on_collection_interaction(
        collection_id: str,
        challenge_id: str,
        submission: CaptchaInteractionSubmission,
    ) -> dict[str, object]:
        try:
            broker.assert_collection(challenge_id, collection_id)
            result = broker.submit_interaction(
                challenge_id,
                action=submission.action,
                x_ratio=submission.x_ratio,
                y_ratio=submission.y_ratio,
            )
        except CaptchaChallengeNotFoundError as exc:
            raise _not_found(exc) from exc
        except CaptchaChallengeStateError as exc:
            raise _state_conflict(exc) from exc
        except ValueError as exc:
            raise _invalid_action(exc) from exc
        return _public_interaction(result)
