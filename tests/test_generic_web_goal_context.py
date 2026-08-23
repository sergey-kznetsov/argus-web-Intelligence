from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.sources.base import SourceTask
from argus.sources.generic_web import GenericWebAdapter


class FakeSnapshots:
    async def capture(self, source_id, url, text, content_type, *, collection_id=None):
        del source_id, url, text, content_type, collection_id
        return SimpleNamespace(snapshot_id="snapshot-1")


def request() -> CollectionRequest:
    return CollectionRequest(
        consumer="test",
        analysis_id="analysis-1",
        territory={"city": "Ижевск"},
        intents=["public_mentions", "local_news"],
        constraints={"max_depth": 2, "max_pages": 10},
    )


@pytest.mark.asyncio
async def test_generic_web_preserves_research_goals_in_evidence_and_child_tasks():
    adapter = GenericWebAdapter(
        fast=object(),
        browser=object(),
        snapshots=FakeSnapshots(),
    )
    task = SourceTask(
        source_id="generic_web",
        goal="public_mentions",
        url="https://example.com/page",
        metadata={
            "collection_id": "collection-1",
            "research_goals": ["public_mentions", "local_news"],
        },
    )
    fetched = FetchResult(
        url=task.url,
        final_url=task.url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        text="<html><head><title>Page</title></head><body>Fact</body></html>",
        title="Page",
        links=["https://example.com/next"],
        runtime="fast",
    )

    result = await adapter.extract(task, fetched, request())

    observation = result.observations[0]
    assert observation.data["research_goals"] == ["public_mentions", "local_news"]
    assert observation.provenance["research_goals"] == ["public_mentions", "local_news"]
    assert result.evidence[0].metadata["research_goals"] == [
        "public_mentions",
        "local_news",
    ]
    child = next(item for item in result.discovered_tasks if item.url.endswith("/next"))
    assert child.metadata["research_goals"] == ["public_mentions", "local_news"]