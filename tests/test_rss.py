from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.history.snapshots import SnapshotService
from argus.sources.base import SourceTask
from argus.sources.rss import RSSAdapter
from argus.storage.sqlite import SQLiteRepository


class FastStub:
    pass


@pytest.mark.asyncio
async def test_rss_extracts_entries(tmp_path: Path):
    repo = SQLiteRepository(tmp_path / "db.sqlite")
    await repo.initialize()
    adapter = RSSAdapter(FastStub(), SnapshotService(repo))
    request = CollectionRequest(consumer="test", analysis_id="1", territory={"city": "Ижевск"},
                                intents=["local_news"])
    task = SourceTask(source_id="rss_atom", goal="local_news", url="https://example.com/feed.xml",
                      metadata={"collection_id": "c1"})
    fetched = SimpleNamespace(blocked=False, final_url="https://example.com/feed.xml",
                              text="<rss><channel><item><title>A</title><link>https://example.com/a</link><description>B</description></item></channel></rss>",
                              content_type="application/rss+xml")
    result = await adapter.extract(task, fetched, request)
    assert len(result.observations) == 1
    assert result.observations[0].title == "A"
    assert len(result.evidence) == 1
