from argus.extraction.atomic_html import extract_atomic_html_blocks


def test_extracts_source_declared_article_without_navigation_shell():
    html = """
    <html><body>
      <nav>Главная Новости Карта Контакты</nav>
      <article>
        <h2>Жители пожаловались на освещение во дворе</h2>
        <time datetime="2026-09-08T18:30:00+04:00">8 сентября</time>
        <p>На Пушкинской улице несколько дней не работает часть фонарей.</p>
        <p>Жители направили обращение в городскую диспетчерскую и ждут ремонта.</p>
        <footer>Поделиться Подписаться</footer>
      </article>
    </body></html>
    """

    result = extract_atomic_html_blocks(html, content_type="text/html")

    assert len(result.items) == 1
    item = result.items[0]
    assert item.title == "Жители пожаловались на освещение во дворе"
    assert item.selector_kind == "article"
    assert item.declared_time == "2026-09-08T18:30:00+04:00"
    assert "Пушкинской улице" in item.text
    assert "городскую диспетчерскую" in item.text
    assert "Главная Новости" not in item.text
    assert "Поделиться Подписаться" not in item.text


def test_generic_css_card_is_not_promoted_to_atomic_observation():
    html = """
    <html><body>
      <div class="news-card complaint-card result-item">
        <h2>Технический заголовок списка</h2>
        <p>Этот блок похож на карточку только по CSS-классам, чего недостаточно.</p>
        <p>Дополнительный текст специально делает карточку достаточно длинной.</p>
      </div>
    </body></html>
    """

    result = extract_atomic_html_blocks(html, content_type="text/html")

    assert result.items == ()


def test_role_article_and_itemprop_articlebody_are_source_declared_semantics():
    html = """
    <html><body>
      <section role="article">
        <h3>Обращение жителей района</h3>
        <p>Жители сообщили о повреждённом покрытии тротуара возле жилых домов.</p>
        <p>В обращении указано, что проблема сохраняется больше недели.</p>
      </section>
      <section itemprop="articleBody">
        <h2>Ремонт участка дороги</h2>
        <p>Муниципальная публикация сообщает о начале ремонта дорожного участка.</p>
        <p>На время работ вводится временное ограничение движения транспорта.</p>
      </section>
    </body></html>
    """

    result = extract_atomic_html_blocks(html, content_type="text/html")

    assert [item.selector_kind for item in result.items] == [
        "role_article",
        "itemprop_articlebody",
    ]


def test_non_html_payload_is_not_atomized():
    result = extract_atomic_html_blocks(
        '<article><h2>Заголовок</h2><p>' + ('текст ' * 40) + "</p></article>",
        content_type="application/json",
    )

    assert result.items == ()


def test_extraction_limit_is_explicitly_reported():
    article = """
      <article>
        <h2>Публикация {number}</h2>
        <p>{body}</p>
      </article>
    """
    html = "<html><body>" + "".join(
        article.format(number=index, body="Содержательный текст публикации. " * 8)
        for index in range(3)
    ) + "</body></html>"

    result = extract_atomic_html_blocks(
        html,
        content_type="text/html",
        max_items=2,
    )

    assert len(result.items) == 2
    assert result.truncated is True
