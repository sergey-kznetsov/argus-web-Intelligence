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


def observation(
    *,
    url: str,
    entity_type: str = "document",
    source_kind: str = "web_page",
    goals: list[str] | None = None,
    quality: dict[str, object] | None = None,
    text: str = "Ижевск, Пушкинская, 277. Public source-backed content",
) -> Observation:
    data: dict[str, object] = {"name": "Кофейня Север"}
    if goals is not None:
        data["research_goals"] = goals
    return Observation(
        observation_id=f"obs-{abs(hash((url, text)))}",
        collection_id="c1",
        analysis_id="map-source-analysis",
        consumer="map-source-test",
        source="generic_web",
        source_kind=source_kind,
        url=url,
        entity_type=entity_type,
        entity_id="entity-1",
        title="Кофейня Север",
        data=data,
        text=text,
        content_hash="a" * 64,
        provenance={},
        quality=quality or {},
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
    discovered = observation(url="https://example.test/place", source_kind="structured_entity")
    first = planner.queries(request("reviews"), observations=[discovered], limit=4)

    assert len(first) == 4
    assert "Кофейня Север" in first[-1]

    second = planner.queries(
        request("reviews"),
        observations=[discovered],
        seen_queries=set(first),
        limit=4,
    )
    assert second
    assert not set(first).intersection(second)


def test_navigation_only_map_pages_do_not_satisfy_review_coverage():
    planner = PublicMapSourceResearchPlanner(target_sources_per_intent=1)
    shell = observation(
        url="https://yandex.ru/maps/org/example/123/",
        goals=["reviews"],
    )

    assert planner.coverage_counts(request("reviews"), [shell]) == {"reviews": 0}
    assert planner.remaining_intents(request("reviews"), [shell]) == ["reviews"]
    assert planner.queries(request("reviews"), observations=[shell], limit=1)


def test_two_independent_map_review_sources_close_curated_review_gap():
    planner = PublicMapSourceResearchPlanner(target_sources_per_intent=2)
    observations = [
        observation(
            url="https://yandex.ru/maps/org/example/123/reviews/",
            entity_type="review",
            source_kind="microdata",
        ),
        observation(
            url="https://2gis.ru/izhevsk/firm/456/tab/reviews",
            entity_type="review",
            source_kind="microdata",
        ),
    ]

    assert planner.coverage_counts(request("reviews"), observations) == {"reviews": 2}
    assert planner.remaining_intents(request("reviews"), observations) == []
    assert planner.queries(request("reviews"), observations=observations, limit=3) == []


def test_structural_review_from_another_address_does_not_close_map_gap():
    planner = PublicMapSourceResearchPlanner(target_sources_per_intent=1)
    unrelated = observation(
        url="https://2gis.ru/izhevsk/firm/999/tab/reviews",
        entity_type="review",
        source_kind="microdata",
        text="Ижевск, улица Ленина, 10. Отличное место.",
    )

    assert planner.coverage_counts(request("reviews"), [unrelated]) == {"reviews": 0}
    assert planner.remaining_intents(request("reviews"), [unrelated]) == ["reviews"]


def test_tracking_variants_of_same_map_page_do_not_fake_two_sources():
    planner = PublicMapSourceResearchPlanner(target_sources_per_intent=2)
    observations = [
        observation(
            url="https://2gis.ru/izhevsk/firm/456/tab/reviews?utm_source=one#reviews",
            entity_type="review",
            source_kind="microdata",
        ),
        observation(
            url="https://2GIS.ru:443/izhevsk/firm/456/tab/reviews?gclid=two",
            entity_type="review",
            source_kind="microdata",
        ),
    ]

    assert planner.coverage_counts(request("reviews"), observations) == {"reviews": 1}
    assert planner.remaining_intents(request("reviews"), observations) == ["reviews"]
    assert planner.queries(request("reviews"), observations=observations, limit=1)


def test_query_suffix_contains_only_undercovered_map_intents():
    planner = PublicMapSourceResearchPlanner(target_sources_per_intent=2)
    observations = [
        observation(
            url="https://yandex.ru/maps/org/example/123/reviews/",
            entity_type="review",
            source_kind="microdata",
        ),
        observation(
            url="https://2gis.ru/izhevsk/firm/456/tab/reviews",
            entity_type="review",
            source_kind="microdata",
        ),
    ]

    queries = planner.queries(
        request("reviews", "complaints"),
        observations=observations,
        limit=3,
    )

    assert planner.remaining_intents(request("reviews", "complaints"), observations) == [
        "complaints"
    ]
    assert queries
    assert all("жалобы" in query for query in queries)
    assert all("отзывы" not in query for query in queries)


def test_source_metadata_declares_public_web_not_paid_api():
    planner = PublicMapSourceResearchPlanner()
    metadata = planner.source_metadata()

    assert planner.version == "public-map-sources/3"
    assert {item["source_id"] for item in metadata} == {
        "yandex_maps_web",
        "2gis_web",
        "google_maps_web",
    }
    assert all(item["access"] == "public_web_browser" for item in metadata)
    assert all(item["paid_api"] is False for item in metadata)