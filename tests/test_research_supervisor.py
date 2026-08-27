from __future__ import annotations

import pytest

from argus.config import Settings
from argus.contracts.models import CollectionRequest, Observation, StructuredError
from argus.research.supervisor import HeuristicResearchSupervisor, OllamaResearchSupervisor


def request(*intents: str) -> CollectionRequest:
    return CollectionRequest(
        consumer="supervisor-test",
        analysis_id="supervisor-analysis",
        territory={"city": "Пермь", "address": "Комсомольский проспект, 27"},
        intents=list(intents),
        constraints={"max_pages": 30, "max_depth": 2},
    )


def observation(
    observation_id: str,
    *,
    url: str,
    intent: str | None = None,
) -> Observation:
    quality = {"intent_evidence": {intent: True}} if intent else {}
    return Observation(
        observation_id=observation_id,
        collection_id="collection-supervisor",
        analysis_id="supervisor-analysis",
        consumer="supervisor-test",
        source="generic_web",
        source_kind="web_page",
        url=url,
        entity_type="document",
        title="Пермь, Комсомольский проспект, 27",
        text="Фактический материал по адресу Пермь, Комсомольский проспект, 27.",
        content_hash=(observation_id[0] if observation_id else "a") * 64,
        quality=quality,
    )


@pytest.mark.asyncio
async def test_heuristic_supervisor_keeps_factual_gap_open():
    supervisor = HeuristicResearchSupervisor(target_sources_per_intent=2)
    observations = [
        observation("a-one", url="https://example.org/one", intent="reviews"),
    ]

    decision = await supervisor.assess(
        request("reviews", "complaints"),
        observations,
        errors=[],
        seen_queries={"query one"},
        pending_count=0,
        remaining_page_budget=8,
    )

    assert decision.continue_research is True
    assert decision.priority_intents == ["reviews", "complaints"]
    assert "coverage_gap" in decision.flags
    assert decision.model_assisted is False
    assert decision.as_dict()["model_output_is_evidence"] is False
    assert decision.rationale_ru


@pytest.mark.asyncio
async def test_heuristic_supervisor_stops_only_after_factual_target_is_met():
    supervisor = HeuristicResearchSupervisor(target_sources_per_intent=2)
    observations = [
        observation("a-one", url="https://example.org/one", intent="reviews"),
        observation("b-two", url="https://example.net/two", intent="reviews"),
    ]

    decision = await supervisor.assess(
        request("reviews"),
        observations,
        errors=[],
        seen_queries=set(),
        pending_count=0,
        remaining_page_budget=8,
    )

    assert decision.continue_research is False
    assert decision.priority_intents == []
    assert "coverage_gap" not in decision.flags


@pytest.mark.asyncio
async def test_heuristic_supervisor_records_blocking_and_budget_signals():
    supervisor = HeuristicResearchSupervisor(target_sources_per_intent=2)

    decision = await supervisor.assess(
        request("complaints"),
        [],
        errors=[
            StructuredError(
                code="DISCOVERY_BLOCKED",
                message="blocked",
                retryable=True,
                source_id="discovery",
            )
        ],
        seen_queries={f"query-{index}" for index in range(12)},
        pending_count=1,
        remaining_page_budget=2,
    )

    assert decision.continue_research is True
    assert "blocked_sources_present" in decision.flags
    assert "query_repetition_risk" in decision.flags
    assert "budget_low" in decision.flags
    assert "pending_work_present" in decision.flags


def test_llm_supervisor_cannot_remove_deterministic_gaps_or_repeat_queries():
    supervisor = OllamaResearchSupervisor(Settings(browser_serp_enabled=False))
    baseline = type("Decision", (), {})()
    baseline.continue_research = True
    baseline.priority_intents = ["reviews", "complaints"]
    baseline.flags = ["coverage_gap"]
    baseline.rationale_ru = "Есть разрывы покрытия."

    decision = supervisor._validated_decision(
        {
            "continue_research": False,
            "priority_intents": ["reviews", "invented_intent"],
            "query_hints": [
                "уже использованный запрос",
                '"Пермь, Комсомольский проспект, 27" жалобы жители',
            ],
            "flags": ["source_diversity_low"],
            "rationale_ru": "Нужно проверить другой тип источников.",
        },
        baseline,
        request("reviews", "complaints"),
        {"уже использованный запрос"},
    )

    assert decision.continue_research is True
    assert decision.priority_intents == ["reviews", "complaints"]
    assert decision.query_hints == ['"Пермь, Комсомольский проспект, 27" жалобы жители']
    assert "invented_intent" not in decision.priority_intents
    assert "source_diversity_low" in decision.flags
    assert decision.model_assisted is True
    assert decision.rationale_ru == "Нужно проверить другой тип источников."
    assert decision.version == "research-supervisor-ollama/2"


def test_llm_supervisor_query_hints_cannot_drift_from_exact_territory():
    supervisor = OllamaResearchSupervisor(Settings(browser_serp_enabled=False))
    baseline = type("Decision", (), {})()
    baseline.continue_research = True
    baseline.priority_intents = ["complaints"]
    baseline.flags = ["coverage_gap"]
    baseline.rationale_ru = "Есть разрыв покрытия."

    decision = supervisor._validated_decision(
        {
            "priority_intents": ["complaints"],
            "query_hints": [
                "complaints near Peremysk district 27",
                "{'queries': ['broken serialized query']",
            ],
        },
        baseline,
        request("complaints"),
        set(),
    )

    assert decision.query_hints == [
        '"Пермь, Комсомольский проспект, 27" complaints near Peremysk district 27'
    ]
