from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.sources.base import SourceTask
from argus.sources.mingkh_residential import MingkhResidentialAdapter


class _Snapshots:
    async def capture(self, *args, **kwargs):
        return SimpleNamespace(snapshot_id="snapshot-navigation-merge")


class _GuidedWeb:
    def __init__(self, guided: FetchResult) -> None:
        self.guided = guided
        self.navigation_calls = 0
        self.final_result = None

    async def navigate_with_agent(self, task, *, context_fetch):
        del task, context_fetch
        self.navigation_calls += 1
        return self.guided

    async def finalize_navigation_goal(self, task, request, result):
        del task, request
        self.final_result = result

    async def health(self):
        return {"status": "ok"}


def _request() -> CollectionRequest:
    return CollectionRequest(
        consumer="merge-test",
        analysis_id="merge-test",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=["residential_premises_count", "residential_population"],
    )


def _page(html: str, *, metadata: dict[str, object] | None = None) -> FetchResult:
    return FetchResult(
        url="https://dom.mingkh.ru/perm/perm/123456",
        final_url="https://dom.mingkh.ru/perm/perm/123456",
        status_code=200,
        content_type="text/html; charset=utf-8",
        text=html,
        title="Дом",
        runtime="browser_recipe" if metadata else "browser",
        metadata=dict(metadata or {}),
    )


@pytest.mark.asyncio
async def test_guided_goal_fact_is_merged_with_existing_residential_fact():
    initial = _page(
        """
        <html><body>
          <h1>Пермь, Комсомольский проспект, 27</h1>
          <div>Количество квартир: 32</div>
        </body></html>
        """
    )
    guided = _page(
        """
        <html><body>
          <h1>Пермь, Комсомольский проспект, 27</h1>
          <div>Количество жителей: 81</div>
        </body></html>
        """,
        metadata={
            "agent_backend": "test-agent",
            "recipe_id": "population-recipe",
            "recipe_version": 1,
        },
    )
    web = _GuidedWeb(guided)
    adapter = MingkhResidentialAdapter(web, _Snapshots())
    task = SourceTask(
        source_id="mingkh_residential",
        goal="residential_population",
        url=initial.url,
        metadata={"collection_id": "merge-test"},
    )

    result = await adapter.extract(task, initial, _request())

    facts = {item.data["intent"]: item.data["value"] for item in result.observations}
    assert facts == {
        "residential_premises_count": 32,
        "residential_population": 81,
    }
    assert result.errors == []
    assert result.partial is False
    assert web.navigation_calls == 1
    assert web.final_result is result
    population = next(
        item for item in result.observations if item.data["intent"] == "residential_population"
    )
    assert population.provenance["recipe_id"] == "population-recipe"
