from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CaptchaAnswerSubmission(BaseModel):
    answer: str = Field(min_length=1, max_length=2048)


class CaptchaChallengeNotFoundError(KeyError):
    pass


class CaptchaChallengeStateError(RuntimeError):
    pass


class FileCaptchaBroker:
    """Cross-process human-in-the-loop CAPTCHA mailbox.

    API and worker processes share the same ARGUS data directory. The browser session stays
    alive in the worker while the API only transports a screenshot and a human-entered
    answer. ARGUS never calls a CAPTCHA-solving service and never derives an answer itself.
    """

    version = "captcha-human-input/1"

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
    ) -> dict[str, object]:
        challenge_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        screenshot_path = self._screenshot_path(challenge_id)
        self._atomic_bytes(screenshot_path, screenshot)
        payload: dict[str, object] = {
            "version": self.version,
            "challenge_id": challenge_id,
            "url": url,
            "kind": kind,
            "prompt": prompt,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "input_selector": input_selector,
            "screenshot_file": screenshot_path.name,
            "answer": None,
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
            if payload.get("status") in {"pending", "answered", "applying"}:
                result.append(self.public(payload))
        return result

    def get(self, challenge_id: str) -> dict[str, object]:
        path = self._state_path(challenge_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CaptchaChallengeNotFoundError(challenge_id) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise CaptchaChallengeStateError("CAPTCHA challenge state is unreadable") from exc

    def public(self, payload: dict[str, object]) -> dict[str, object]:
        return {
            "version": payload.get("version", self.version),
            "challenge_id": payload.get("challenge_id"),
            "url": payload.get("url"),
            "kind": payload.get("kind"),
            "prompt": payload.get("prompt"),
            "status": payload.get("status"),
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "has_screenshot": bool(payload.get("screenshot_file")),
            "manual_input_supported": payload.get("kind") == "text",
            "error": payload.get("error"),
        }

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
                "This CAPTCHA requires interactive browser takeover; text input is unavailable"
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
            if status in {"failed", "expired", "completed"}:
                raise CaptchaChallengeStateError(
                    f"CAPTCHA challenge is no longer answerable: {status}"
                )
            await asyncio.sleep(0.4)
        self.mark_failed(challenge_id, "manual CAPTCHA input timed out", status="expired")
        raise TimeoutError("manual CAPTCHA input timed out")

    def mark_completed(self, challenge_id: str) -> None:
        payload = self.get(challenge_id)
        payload["answer"] = None
        payload["status"] = "completed"
        payload["updated_at"] = datetime.now(UTC).isoformat()
        self._write(challenge_id, payload)

    def mark_failed(self, challenge_id: str, error: str, *, status: str = "failed") -> None:
        payload = self.get(challenge_id)
        payload["answer"] = None
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
