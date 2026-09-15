from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.browser.runtime import BrowserCrawlerRuntime
from argus.crawler.models import FetchResult
from argus.human_interaction import FileCaptchaBroker
from argus.sources.base import SourceTask
from argus.sources.mingkh_residential import MingkhResidentialAdapter


_FINAL_HTML = """<!doctype html>
<html lang="ru">
  <body>
    <h1>Анкета дома г. Пермь, Комсомольский проспект, 27</h1>
    <section>
      <div>Тип дома</div><div>Многоквартирный дом</div>
      <div>Количество квартир</div><div>32</div>
    </section>
  </body>
</html>
"""


class _FakeLocator:
    def __init__(self, page: "_FakePage", selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def count(self) -> int:
        if self.selector == "body":
            return 1
        if self.page.cleared:
            return 0
        if "captcha" in self.selector.casefold():
            return 1
        if self.selector == "button[type='submit']:visible":
            return 1
        return 0

    async def fill(self, answer: str) -> None:
        self.page.answer = answer

    async def click(self) -> None:
        self.page.cleared = True

    async def press(self, key: str) -> None:
        assert key == "Enter"
        self.page.cleared = True

    async def inner_text(self) -> str:
        if self.page.cleared:
            return "Анкета дома г. Пермь, Комсомольский проспект, 27 Количество квартир 32"
        return "Подтвердите, что вы не робот. CAPTCHA"


class _FakePage:
    def __init__(self) -> None:
        self.cleared = False
        self.answer: str | None = None
        self.session_id = object()

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    async def screenshot(self, **_kwargs) -> bytes:
        return b"\x89PNG\r\n\x1a\nfixture"

    async def content(self) -> str:
        if self.cleared:
            return _FINAL_HTML
        return "<html><body>Подтвердите, что вы не робот. CAPTCHA</body></html>"

    async def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None


class _Snapshots:
    async def capture(self, *args, **kwargs):
        return SimpleNamespace(snapshot_id="snapshot-after-captcha")


class _Web:
    async def navigate_with_agent(self, task, *, context_fetch):
        del task, context_fetch
        return None

    async def finalize_navigation_goal(self, task, request, result):
        del task, request, result

    async def health(self):
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_text_captcha_resumes_same_page_and_yields_source_backed_residential_fact(
    tmp_path: Path,
) -> None:
    runtime = object.__new__(BrowserCrawlerRuntime)
    runtime._captcha = FileCaptchaBroker(tmp_path / "human_interaction")
    page = _FakePage()
    original_session = page.session_id

    handling = asyncio.create_task(
        runtime._handle_manual_captcha(
            page,
            "https://dom.mingkh.ru/perm/perm/123456",
            interaction_context={
                "collection_id": "collection-fixture",
                "analysis_id": "janus-fixture-analysis",
                "source_id": "mingkh_residential",
            },
        )
    )

    challenge = None
    for _ in range(100):
        pending = runtime._captcha.list_pending_for_collection("collection-fixture")
        if pending:
            challenge = pending[0]
            break
        await asyncio.sleep(0.01)
    assert challenge is not None
    assert challenge["analysis_id"] == "janus-fixture-analysis"
    assert challenge["source_id"] == "mingkh_residential"
    assert challenge["manual_input_supported"] is True

    runtime._captcha.submit_answer(str(challenge["challenge_id"]), "AB12")
    assert await handling is True
    assert page.session_id is original_session
    assert page.answer == "AB12"
    assert page.cleared is True
    assert runtime._captcha.list_pending_for_collection("collection-fixture") == []

    request = CollectionRequest(
        consumer="janus.parking.potential.uds",
        consumer_profile_version=1,
        capability="residential_facts",
        requested_facts=["residential_premises_count"],
        analysis_id="janus-fixture-analysis",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["residential_premises_count"],
        allow_partial=False,
    )
    task = SourceTask(
        source_id="mingkh_residential",
        goal="residential_premises_count",
        url="https://dom.mingkh.ru/perm/perm/123456",
        metadata={
            "collection_id": "collection-fixture",
            "analysis_id": "janus-fixture-analysis",
        },
    )
    fetched = FetchResult(
        url=task.url,
        final_url=task.url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        text=await page.content(),
        title="Дом",
        blocked=False,
        runtime="browser",
    )
    adapter = MingkhResidentialAdapter(_Web(), _Snapshots())
    result = await adapter.extract(task, fetched, request)

    assert result.partial is False
    assert result.errors == []
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.source == "mingkh_residential"
    assert observation.data["intent"] == "residential_premises_count"
    assert observation.data["value"] == 32
    assert observation.provenance["snapshot_id"] == "snapshot-after-captcha"
    assert len(result.evidence) == 1
    assert result.evidence[0].text == "Количество квартир: 32"
