from pathlib import Path

import pytest

from argus.human_interaction import (
    CaptchaChallengeNotFoundError,
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


def test_collection_scoped_listing_never_leaks_other_analysis_challenge(tmp_path: Path):
    broker = FileCaptchaBroker(tmp_path)
    challenge_a = broker.create(
        url="https://dom.mingkh.ru/a",
        screenshot=b"a",
        kind="text",
        prompt="A",
        input_selector="input[name=captcha]",
        collection_id="collection-a",
        analysis_id="analysis-a",
        source_id="mingkh_residential",
    )
    challenge_b = broker.create(
        url="https://dom.mingkh.ru/b",
        screenshot=b"b",
        kind="text",
        prompt="B",
        input_selector="input[name=captcha]",
        collection_id="collection-b",
        analysis_id="analysis-b",
        source_id="mingkh_residential",
    )

    pending_a = broker.list_pending_for_collection("collection-a")
    assert [item["challenge_id"] for item in pending_a] == [challenge_a["challenge_id"]]
    assert challenge_b["challenge_id"] not in {item["challenge_id"] for item in pending_a}


def test_collection_guard_hides_cross_collection_challenge_identity(tmp_path: Path):
    broker = FileCaptchaBroker(tmp_path)
    challenge = broker.create(
        url="https://dom.mingkh.ru/a",
        screenshot=b"a",
        kind="text",
        prompt="A",
        input_selector="input[name=captcha]",
        collection_id="collection-a",
        analysis_id="analysis-a",
        source_id="mingkh_residential",
    )
    challenge_id = str(challenge["challenge_id"])

    with pytest.raises(CaptchaChallengeNotFoundError):
        broker.assert_collection(challenge_id, "collection-b")


def test_public_payload_never_exposes_answer_selector_or_filesystem_path(tmp_path: Path):
    broker = FileCaptchaBroker(tmp_path)
    challenge = broker.create(
        url="https://dom.mingkh.ru/a",
        screenshot=b"a",
        kind="text",
        prompt="A",
        input_selector="input[name=captcha-secret]",
        collection_id="collection-a",
        analysis_id="analysis-a",
        source_id="mingkh_residential",
    )
    challenge_id = str(challenge["challenge_id"])
    public = broker.submit_answer(challenge_id, "SECRET")

    assert "answer" not in public
    assert "input_selector" not in public
    assert "screenshot_file" not in public
    assert "SECRET" not in str(public)
    assert "captcha-secret" not in str(public)


def test_refresh_is_bounded_interaction_and_preserves_correlation(tmp_path: Path):
    broker = FileCaptchaBroker(tmp_path)
    challenge = broker.create(
        url="https://dom.mingkh.ru/a",
        screenshot=b"a",
        kind="interactive",
        prompt="A",
        input_selector=None,
        collection_id="collection-a",
        analysis_id="analysis-a",
        source_id="mingkh_residential",
    )
    challenge_id = str(challenge["challenge_id"])
    broker.mark_interactive_required(challenge_id)

    public = broker.submit_interaction(challenge_id, action="refresh")
    stored = broker.get(challenge_id)

    assert public["collection_id"] == "collection-a"
    assert public["analysis_id"] == "analysis-a"
    assert public["source_id"] == "mingkh_residential"
    assert public["interaction_count"] == 1
    assert stored["interaction"] == {"action": "refresh"}
