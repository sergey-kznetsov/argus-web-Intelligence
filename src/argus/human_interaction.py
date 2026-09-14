from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CaptchaAnswerSubmission(BaseModel):
    answer: str = Field(min_length=1, max_length=2048)


class CaptchaInteractionSubmission(BaseModel):
    action: Literal["click", "refresh"]
    x_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    y_ratio: float | None = Field(default=None, ge=0.0, le=1.0)


class CaptchaChallengeNotFoundError(KeyError):
    pass


class CaptchaChallengeStateError(RuntimeError):
    pass


class FileCaptchaBroker:
    """Cross-process human-in-the-loop CAPTCHA mailbox.

    The worker keeps the browser session alive. The API transports only a screenshot and
    bounded user input. Correlation fields are safe identifiers only: cookies, bearer tokens,
    selectors and filesystem paths never leave the broker's private state.
    """

    version = "captcha-human-input/4"
    visible_statuses = frozenset(
        {
            "pending",
            "answered",
            "applying",
            "interactive_required",
            "interaction_queued",
            "interaction_applying",
        }
    )

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        url: str,
        screenshot: bytes,
        kind: str,
        prompt: str,
        input_selector: str | None,
        collection_id: str | None = None,
        analysis_id: str | None = None,
        source_id: str | None = None,
    ) -> dict[str, object]:
        challenge_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        screenshot_path = self._screenshot_path(challenge_id)
        self._atomic_bytes(screenshot_path, screenshot)
        payload: dict[str, object] = {
            "version": self.version,
            "challenge_id": challenge_id,
            "interaction_id": challenge_id,
            "collection_id": str(collection_id or "").strip() or None,
            "analysis_id": str(analysis_id or "").strip() or None,
            "source_id": str(source_id or "").strip() or None,
            "url": url,
            "kind": kind,
            "prompt": prompt,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "input_selector": input_selector,
            "screenshot_file": screenshot_path.name,
            "answer": None,
            "interaction": None,
            "interaction_count": 0,
            "error": None,
        }
        self._write(challenge_id, payload)
        return self.public(payload)

    def list_pending(self) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("status") in self.visible_statuses:
                result.append(self.public(payload))
        return result

    def list_pending_for_collection(self, collection_id: str) -> list[dict[str, object]]:
        expected = str(collection_id).strip()
        return [
            item
            for item in self.list_pending()
            if str(item.get("collection_id") or "").strip() == expected
        ]

    def get(self, challenge_id: str) -> dict[str, object]:
        path = self._state_path(challenge_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CaptchaChallengeNotFoundError(challenge_id) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise CaptchaChallengeStateError("CAPTCHA challenge state is unreadable") from exc

    def public(self, payload: dict[str, object]) -> dict[str, object]:
        status = payload.get("status")
        kind = payload.get("kind")
        challenge_id = payload.get("challenge_id")
        return {
            "version": payload.get("version", self.version),
            "challenge_id": challenge_id,
            "interaction_id": payload.get("interaction_id") or challenge_id,
            "collection_id": payload.get("collection_id"),
            "analysis_id": payload.get("analysis_id"),
            "source_id": payload.get("source_id"),
            "url": payload.get("url"),
            "kind": kind,
            "prompt": payload.get("prompt"),
            "status": status,
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "has_screenshot": bool(payload.get("screenshot_file")),
            "manual_input_supported": kind == "text",
            "interactive_required": kind == "interactive" and status in self.visible_statuses,
            "interactive_input_supported": kind == "interactive",
            "interaction_count": int(payload.get("interaction_count", 0) or 0),
            "error": payload.get("error"),
        }

    def assert_collection(self, challenge_id: str, collection_id: str) -> dict[str, object]:
        payload = self.get(challenge_id)
        actual = str(payload.get("collection_id") or "").strip()
        expected = str(collection_id).strip()
        if not actual or actual != expected:
            # Do not reveal whether the challenge exists for another collection.
            raise CaptchaChallengeNotFoundError(challenge_id)
        return payload

    def screenshot_path(self, challenge_id: str) -> Path:
        payload = self.get(challenge_id)
        file_name = str(payload.get("screenshot_file") or "")
        if not file_name:
            raise CaptchaChallengeNotFoundError(challenge_id)
        path = self.root / Path(file_name).name
        if not path.is_file():
            raise CaptchaChallengeNotFoundError(challenge_id)
        return path

    def submit_answer(self, challenge_id: str, answer: str) -> dict[str, object]:
        answer = answer.strip()
        if not answer:
            raise ValueError("CAPTCHA answer must not be blank")
        payload = self.get(challenge_id)
        if payload.get("kind") != "text":
            raise CaptchaChallengeStateError(
                "This CAPTCHA requires interactive browser input; text input is unavailable"
            )
        if payload.get("status") != "pending":
            raise CaptchaChallengeStateError(
                f"CAPTCHA challenge is not waiting for input: {payload.get('status')}"
            )
        payload["answer"] = answer
        payload["status"] = "answered"
        payload["updated_at"] = datetime.now(UTC).isoformat()
        self._write(challenge_id, payload)
        return self.public(payload)

    def mark_interactive_required(self, challenge_id: str, *, error: str | None = None) -> None:
        payload = self.get(challenge_id)
        if payload.get("kind") != "interactive":
            raise CaptchaChallengeStateError("challenge is not interactive")
        payload["answer"] = None
        payload["interaction"] = None
        payload["status"] = "interactive_required"
        payload["error"] = error[:500] if error else None
        payload["updated_at"] = datetime.now(UTC).isoformat()
        self._write(challenge_id, payload)

    def submit_interaction(
        self,
        challenge_id: str,
        *,
        action: str,
        x_ratio: float | None = None,
        y_ratio: float | None = None,
    ) -> dict[str, object]:
        payload = self.get(challenge_id)
        if payload.get("kind") != "interactive":
            raise CaptchaChallengeStateError("challenge does not accept interactive input")
        if payload.get("status") != "interactive_required":
            raise CaptchaChallengeStateError(
                f"interactive challenge is not waiting for input: {payload.get('status')}"
            )
        action = str(action).strip().casefold()
        if action not in {"click", "refresh"}:
            raise ValueError("unsupported interactive CAPTCHA action")
        if action == "click":
            if x_ratio is None or y_ratio is None:
                raise ValueError("click requires x_ratio and y_ratio")
            if not 0.0 <= float(x_ratio) <= 1.0 or not 0.0 <= float(y_ratio) <= 1.0:
                raise ValueError("click coordinates must be normalized to 0..1")
        interaction: dict[str, object] = {"action": action}
        if action == "click":
            interaction["x_ratio"] = float(x_ratio)
            interaction["y_ratio"] = float(y_ratio)
        payload["interaction"] = interaction
        payload["interaction_count"] = int(payload.get("interaction_count", 0) or 0) + 1
        payload["status"] = "interaction_queued"
        payload["error"] = None
        payload["updated_at"] = datetime.now(UTC).isoformat()
        self._write(challenge_id, payload)
        return self.public(payload)

    async def wait_for_answer(self, challenge_id: str, *, timeout_seconds: float) -> str:
        deadline = time.monotonic() + max(1.0, timeout_seconds)
        while time.monotonic() < deadline:
            payload = self.get(challenge_id)
            status = payload.get("status")
            if status == "answered":
                answer = str(payload.get("answer") or "")
                if not answer:
                    raise CaptchaChallengeStateError("CAPTCHA answer is empty")
                payload["answer"] = None
                payload["status"] = "applying"
                payload["updated_at"] = datetime.now(UTC).isoformat()
                self._write(challenge_id, payload)
                return answer
            if status in {"failed", "expired", "completed", "interactive_required"}:
                raise CaptchaChallengeStateError(
                    f"CAPTCHA challenge is no longer answerable as text: {status}"
                )
            await asyncio.sleep(0.4)
        self.mark_failed(challenge_id, "manual CAPTCHA input timed out", status="expired")
        raise TimeoutError("manual CAPTCHA input timed out")

    async def wait_for_interaction(
        self,
        challenge_id: str,
        *,
        timeout_seconds: float,
    ) -> dict[str, object]:
        deadline = time.monotonic() + max(1.0, timeout_seconds)
        while time.monotonic() < deadline:
            payload = self.get(challenge_id)
            status = payload.get("status")
            if status == "interaction_queued":
                interaction = payload.get("interaction")
                if not isinstance(interaction, dict):
                    raise CaptchaChallengeStateError("interactive CAPTCHA action is unreadable")
                payload["interaction"] = None
                payload["status"] = "interaction_applying"
                payload["updated_at"] = datetime.now(UTC).isoformat()
                self._write(challenge_id, payload)
                return dict(interaction)
            if status in {"failed", "expired", "completed"}:
                raise CaptchaChallengeStateError(
                    f"CAPTCHA challenge is no longer interactive: {status}"
                )
            await asyncio.sleep(0.25)
        self.mark_failed(challenge_id, "interactive CAPTCHA input timed out", status="expired")
        raise TimeoutError("interactive CAPTCHA input timed out")

    def update_interactive_screenshot(self, challenge_id: str, screenshot: bytes) -> None:
        payload = self.get(challenge_id)
        if payload.get("kind") != "interactive":
            raise CaptchaChallengeStateError("challenge is not interactive")
        self._atomic_bytes(self._screenshot_path(challenge_id), screenshot)
        payload["interaction"] = None
        payload["status"] = "interactive_required"
        payload["error"] = None
        payload["updated_at"] = datetime.now(UTC).isoformat()
        self._write(challenge_id, payload)

    def mark_completed(self, challenge_id: str) -> None:
        payload = self.get(challenge_id)
        payload["answer"] = None
        payload["interaction"] = None
        payload["status"] = "completed"
        payload["updated_at"] = datetime.now(UTC).isoformat()
        self._write(challenge_id, payload)

    def mark_failed(self, challenge_id: str, error: str, *, status: str = "failed") -> None:
        payload = self.get(challenge_id)
        payload["answer"] = None
        payload["interaction"] = None
        payload["status"] = status
        payload["error"] = error[:500]
        payload["updated_at"] = datetime.now(UTC).isoformat()
        self._write(challenge_id, payload)

    def _state_path(self, challenge_id: str) -> Path:
        self._validate_id(challenge_id)
        return self.root / f"{challenge_id}.json"

    def _screenshot_path(self, challenge_id: str) -> Path:
        self._validate_id(challenge_id)
        return self.root / f"{challenge_id}.png"

    @staticmethod
    def _validate_id(challenge_id: str) -> None:
        try:
            UUID(challenge_id)
        except (ValueError, AttributeError) as exc:
            raise CaptchaChallengeNotFoundError(str(challenge_id)) from exc

    def _write(self, challenge_id: str, payload: dict[str, object]) -> None:
        path = self._state_path(challenge_id)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self._atomic_bytes(path, raw.encode("utf-8"))

    @staticmethod
    def _atomic_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_bytes(content)
        os.replace(temp, path)
