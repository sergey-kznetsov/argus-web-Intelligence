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

    broker.mark_interactive_required(challenge_id)

    pending = broker.list_pending()
    assert len(pending) == 1
    assert pending[0]["challenge_id"] == challenge_id
    assert pending[0]["status"] == "interactive_required"
    assert pending[0]["interactive_required"] is True
    assert pending[0]["interactive_input_supported"] is True
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


def test_interactive_challenge_queues_only_bounded_click(tmp_path: Path):
    broker = FileCaptchaBroker(tmp_path)
    challenge = broker.create(
        url="https://example.test/challenge",
        screenshot=b"first",
        kind="interactive",
        prompt="Кликните по проверке.",
        input_selector=None,
    )
    challenge_id = str(challenge["challenge_id"])
    broker.mark_interactive_required(challenge_id)

    public = broker.submit_interaction(
        challenge_id,
        action="click",
        x_ratio=0.25,
        y_ratio=0.75,
    )
    stored = broker.get(challenge_id)

    assert public["status"] == "interaction_queued"
    assert public["interaction_count"] == 1
    assert "interaction" not in public
    assert stored["interaction"] == {
        "action": "click",
        "x_ratio": 0.25,
        "y_ratio": 0.75,
    }


@pytest.mark.asyncio
async def test_worker_consumes_interaction_without_exposing_coordinates(tmp_path: Path):
    broker = FileCaptchaBroker(tmp_path)
    challenge = broker.create(
        url="https://example.test/challenge",
        screenshot=b"first",
        kind="interactive",
        prompt="Кликните по проверке.",
        input_selector=None,
    )
    challenge_id = str(challenge["challenge_id"])
    broker.mark_interactive_required(challenge_id)
    broker.submit_interaction(
        challenge_id,
        action="click",
        x_ratio=0.1,
        y_ratio=0.2,
    )

    interaction = await broker.wait_for_interaction(challenge_id, timeout_seconds=1)
    public = broker.public(broker.get(challenge_id))

    assert interaction == {"action": "click", "x_ratio": 0.1, "y_ratio": 0.2}
    assert public["status"] == "interaction_applying"
    assert "interaction" not in public


def test_interactive_screenshot_refresh_returns_to_operator(tmp_path: Path):
    broker = FileCaptchaBroker(tmp_path)
    challenge = broker.create(
        url="https://example.test/challenge",
        screenshot=b"first",
        kind="interactive",
        prompt="Кликните по проверке.",
        input_selector=None,
    )
    challenge_id = str(challenge["challenge_id"])
    broker.mark_interactive_required(challenge_id)

    broker.update_interactive_screenshot(challenge_id, b"second")

    assert broker.get(challenge_id)["status"] == "interactive_required"
    assert broker.screenshot_path(challenge_id).read_bytes() == b"second"


def test_interactive_click_rejects_missing_or_out_of_range_coordinates(tmp_path: Path):
    broker = FileCaptchaBroker(tmp_path)
    challenge = broker.create(
        url="https://example.test/challenge",
        screenshot=b"png",
        kind="interactive",
        prompt="Кликните по проверке.",
        input_selector=None,
    )
    challenge_id = str(challenge["challenge_id"])
    broker.mark_interactive_required(challenge_id)

    with pytest.raises(ValueError):
        broker.submit_interaction(challenge_id, action="click")
    with pytest.raises(ValueError):
        broker.submit_interaction(
            challenge_id,
            action="click",
            x_ratio=1.5,
            y_ratio=0.5,
        )
