from types import SimpleNamespace

import pytest

from argus.contracts.models import CollectionRequest
from argus.crawler.models import FetchResult
from argus.sources.base import SourceTask
from argus.sources.generic_web import GenericWebAdapter


class FakeSnapshots:
    async def capture(self, source_id, url, text, content_type):
        del source_id, url, text, content_type
        return SimpleNamespace(snapshot_id="snapshot-jsonld")


@pytest.mark.asyncio
async def test_generic_web_emits_evidence_backed_json_ld_entities():
    adapter = GenericWebAdapter(
        fast=object(),
        browser=object(),
        snapshots=FakeSnapshots(),
    )
    request = CollectionRequest(
        consumer="test",
        analysis_id="analysis-jsonld",
        territory={"city": "Ижевск"},
        intents=["historical_context"],
        constraints={"max_depth": 0},
    )
    task = SourceTask(
        source_id="generic_web",
        goal="historical_context",
        url="https://example.com/place",
        metadata={
            "collection_id": "collection-jsonld",
            "research_goals": ["historical_context"],
        },
    )
    html = """
    <html><head><title>Объект</title>
      <script type="application/ld+json">
        {
          "@context":"https://schema.org",
          "@id":"https://example.com/place#entity",
          "@type":"Place",
          "name":"Дом купца Иванова",
          "description":"Историческое здание"
        }
      </script>
    </head><body>Основной текст страницы</body></html>
    """
    fetched = FetchResult(
        url=task.url,
        final_url=task.url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        text=html,
        title="Объект",
        runtime="fast",
    )

    result = await adapter.extract(task, fetched, request)

    assert len(result.observations) == 2
    page = next(item for item in result.observations if item.source_kind == "web_page")
    entity = next(item for item in result.observations if item.source_kind == "json_ld")
    entity_evidence = next(item for item in result.evidence if item.type == "json_ld")

    assert page.data["json_ld_summary"]["entities"] == 1
    assert entity.entity_id == "https://example.com/place#entity"
    assert entity.title == "Дом купца Иванова"
    assert entity.data["name"] == "Дом купца Иванова"
    assert entity.provenance["snapshot_id"] == "snapshot-jsonld"
    assert entity.provenance["json_ld"]["remote_contexts_resolved"] is False
    assert entity_evidence.source.url == "https://example.com/place"
    assert entity_evidence.metadata["remote_contexts_resolved"] is False
