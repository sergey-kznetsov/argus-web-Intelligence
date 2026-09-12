from argus.crawler.block_detection import (
    looks_like_blocked_page,
    looks_like_captcha_page,
    looks_like_transient_challenge_page,
)


def test_transient_browser_challenge_is_not_treated_as_text_captcha():
    html = "Checking your browser. Please wait while we verify the connection is secure."

    assert looks_like_transient_challenge_page(html, "text/html") is True
    assert looks_like_captcha_page(html, "text/html") is False
    assert looks_like_blocked_page(html, "text/html") is True


def test_explicit_captcha_is_reserved_for_user_handoff():
    html = "Подтвердите, что вы не робот. Введите символы с картинки."

    assert looks_like_captcha_page(html, "text/html") is True
    assert looks_like_transient_challenge_page(html, "text/html") is False
    assert looks_like_blocked_page(html, "text/html") is True


def test_normal_page_is_not_a_challenge():
    html = "Новости района. Жители сообщили о ремонте дороги на Пушкинской улице."

    assert looks_like_captcha_page(html, "text/html") is False
    assert looks_like_transient_challenge_page(html, "text/html") is False
    assert looks_like_blocked_page(html, "text/html") is False
