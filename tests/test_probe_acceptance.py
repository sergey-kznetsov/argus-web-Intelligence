from __future__ import annotations

from types import SimpleNamespace

from argus.cli.probe import build_probe_acceptance
from argus.contracts.models import CollectionRequest, Observation


def _observation(
    *,
    provider: str,
    entity_type: str,
    source_kind: str,
    url: str,
    text: str = "Source-backed page text",
) -> Observation:
    return Observation(
        collection_id="collection-1",
        analysis_id="analysis-1",
        consumer="probe-test",
        source="generic_web",
        source_kind=source_kind,
        url=url,
        entity_type=entity_type,
        title="Map page",
        text=text,
        content_hash="a" * 64,
        provenance={"public_map_source": {"provider": provider}},
    )


def test_map_provider_acceptance_requires_factual_requested_intent() -> None:
    request = CollectionRequest(
        consumer="probe-test",
        analysis_id="analysis-1",
        territory={"city": "Ижевск"},
        intents=["reviews"],
    )
    shell_only = _observation(
        provider="yandex_maps_web",
        entity_type="document",
        source_kind="web_page",
        url="https://yandex.ru/maps/org/example/1/",
    )
    review = _observation(
        provider="2gis_web",
        entity_type="review",
        source_kind="microdata",
        url="https://2gis.ru/izhevsk/firm/1/tab/reviews",
        text="Ижевск. Публичный отзыв о месте.",
    )
    result = SimpleNamespace(observations=[shell_only, review], evidence=[])

    acceptance = build_probe_acceptance(request, result)

    assert acceptance["intent_source_counts"] == {"reviews": 1}
    assert acceptance["covered_intents"] == ["reviews"]
    assert acceptance["public_map_providers_with_facts"] == ["2gis_web"]
