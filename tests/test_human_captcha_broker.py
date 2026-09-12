from pathlib import Path

import pytest

from argus.human_interaction import (
    CaptchaChallengeStateError,
    FileCaptchaBroker,
)


def test_interactive_challenge_remains_visible_to_operator(tmp_path: Path):
    broker = FileCaptchaBroker(tmp_path)
    challenge = broker.create(
        url="https://example.test/challenge",
        screenshot=b"png",
        kind="interactive",
        prompt="Требуется ручное прохождение.",
        input_selector=None,
    )
    challenge_id = str(challenge["challenge_id"])

    broker.mark_failed(
        challenge_id,
        "interactive challenge requires user browser interaction",
        status="interactive_required",
    )

    pending = broker.list_pending()
    assert len(pending) == 1
    assert pending[0]["challenge_id"] == challenge_id
    assert pending[0]["status"] == "interactive_required"
    assert pending[0]["interactive_required"] is True
    assert pending[0]["manual_input_supported"] is False


def test_text_challenge_accepts_answer_without_exposing_it(tmp_path: Path):
    broker = FileCaptchaBroker(tmp_path)
    challenge = broker.create(
        url="https://example.test/captcha",
        screenshot=b"png",
        kind="text",
        prompt="Введите символы.",
        input_selector="input[name=captcha]",
    )
    challenge_id = str(challenge["challenge_id"])

    public = broker.submit_answer(challenge_id, "AB12")
    stored = broker.get(challenge_id)

    assert public["status"] == "answered"
    assert "answer" not in public
    assert stored["answer"] == "AB12"


def test_interactive_challenge_rejects_text_answer(tmp_path: Path):
    broker = FileCaptchaBroker(tmp_path)
    challenge = broker.create(
        url="https://example.test/challenge",
        screenshot=b"png",
        kind="interactive",
        prompt="Требуется ручное прохождение.",
        input_selector=None,
    )

    with pytest.raises(CaptchaChallengeStateError):
        broker.submit_answer(str(challenge["challenge_id"]), "anything")
