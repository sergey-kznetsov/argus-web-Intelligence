from __future__ import annotations

from argus.contracts.models import CollectionRequest, Observation
from argus.research.public_map_sources import PublicMapSourceResearchPlanner


def request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="map-source-test",
        analysis_id="map-source-analysis",
        territory={"city": "Ижевск", "address": "Пушкинская, 277"},
        intents=list(intents),
    )


def test_public_map_queries_cover_all_curated_platforms_for_reviews():
    planner = PublicMapSourceResearchPlanner()

    queries = planner.queries(request("reviews"), limit=3)

    assert len(queries) == 3
    assert queries[0].startswith('site:yandex.ru/maps "Ижевск, Пушкинская, 277"')
    assert queries[1].startswith('site:2gis.ru "Ижевск, Пушкинская, 277"')
    assert queries[2].startswith('site:google.com/maps "Ижевск, Пушкинская, 277"')
    assert all("отзывы" in query for query in queries)


def test_public_map_queries_do_not_run_for_unrelated_intents():
    planner = PublicMapSourceResearchPlanner()

    assert planner.queries(request("historical_context"), limit=3) == []


def test_public_map_queries_expand_to_discovered_entity_names_and_dedupe_seen():
    planner = PublicMapSourceResearchPlanner()
    observation = Observation(
        observation_id="o1",
        collection_id="c1",
        analysis_id="map-source-analysis",
        consumer="map-source-test",
        source="generic_web",
        source_kind="structured_entity",
        url="https://example.test/place",
        entity_type="organization",
        entity_id="org1",
        title="Кофейня Север",
        data={"name": "Кофейня Север"},
        content_hash="a" * 64,
        provenance={},
        quality={},
    )
    first = planner.queries(request("reviews"), observations=[observation], limit=4)

    assert len(first) == 4
    assert "Кофейня Север" in first[-1]

    second = planner.queries(
        request("reviews"),
        observations=[observation],
        seen_queries=set(first),
        limit=4,
    )
    assert second
    assert not set(first).intersection(second)


def test_source_metadata_declares_public_web_not_paid_api():
    metadata = PublicMapSourceResearchPlanner().source_metadata()

    assert {item["source_id"] for item in metadata} == {
        "yandex_maps_web",
        "2gis_web",
        "google_maps_web",
    }
    assert all(item["access"] == "public_web_browser" for item in metadata)
    assert all(item["paid_api"] is False for item in metadata)
