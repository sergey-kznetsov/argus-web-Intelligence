import json

import httpx
import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest
from argus.research.searxng import SearxngDiscoveryProvider


@pytest.mark.asyncio
async def test_searxng_provider_uses_json_api_and_deduplicates_results(tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = {
            "results": [
                {
                    "url": "https://example.com/a",
                    "title": "A",
                    "engines": ["google", "bing"],
                },
                {
                    "url": "https://example.com/a",
                    "title": "A duplicate",
                    "engines": ["bing"],
                },
                {
                    "url": "https://example.org/b",
                    "title": "B",
                    "engine": "duckduckgo",
                },
            ]
        }
        return httpx.Response(200, content=json.dumps(body).encode(), request=request)

    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
        searxng_url="http://127.0.0.1:8888",
        searxng_max_results_per_query=10,
    )
    provider = SearxngDiscoveryProvider(settings, transport=httpx.MockTransport(handler))
    request = CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
        constraints={"language": "ru"},
    )

    hits = await provider.discover(["Ижевск новости"], request)

    assert [hit.url for hit in hits] == ["https://example.com/a", "https://example.org/b"]
    assert hits[0].engines == ["google", "bing"]
    assert hits[1].engines == ["duckduckgo"]
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/search"
    assert b"format=json" in requests[0].content
    assert b"language=ru" in requests[0].content


@pytest.mark.asyncio
async def test_searxng_response_size_is_limited(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 2048, request=request)

    settings = Settings(
        db_path=tmp_path / "db.sqlite",
        token_file=tmp_path / "token",
        searxng_url="http://127.0.0.1:8888",
        max_response_bytes=1024,
    )
    provider = SearxngDiscoveryProvider(settings, transport=httpx.MockTransport(handler))
    request = CollectionRequest(
        consumer="test",
        analysis_id="1",
        territory={"city": "Ижевск"},
        intents=["public_mentions"],
    )

    with pytest.raises(ValueError, match="exceeds configured limit"):
        await provider.discover(["query"], request)
