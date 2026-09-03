from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from argus.cli.probe import render_probe_summary, run_embedded_probe
from argus.config import Settings
from argus.live_acceptance import (
    acceptance_failures,
    build_profile_request,
    error_details,
    mandatory_janus_source_blocked,
    query_shape_violations,
)
from argus.web.profiles import web_test_profiles

ADDRESS = "Комсомольский проспект, 27"
CITY = "Пермь"
PROFILE_IDS = ("kraken", "janus", "historical")


def _overview(profile_id: str, report: dict[str, object]) -> dict[str, object]:
    collection = report.get("collection")
    collection = collection if isinstance(collection, dict) else {}
    result = report.get("result")
    result = result if isinstance(result, dict) else {}
    acceptance = report.get("acceptance")
    acceptance = acceptance if isinstance(acceptance, dict) else {}
    checkpoint = collection.get("checkpoint")
    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}
    observations = result.get("observations")
    evidence = result.get("evidence")
    errors = result.get("errors")
    normalized_errors = error_details(errors)
    mandatory_source_blocked = mandatory_janus_source_blocked(
        profile_id=profile_id,
        status=str(result.get("status") or ""),
        observation_count=len(observations) if isinstance(observations, list) else 0,
        evidence_count=len(evidence) if isinstance(evidence, list) else 0,
        error_details=normalized_errors,
    )
    return {
        "profile": profile_id,
        "status": result.get("status"),
        "fully_covered": acceptance.get("fully_covered"),
        "covered_intents": acceptance.get("covered_intents", []),
        "uncovered_intents": acceptance.get("uncovered_intents", []),
        "intent_source_counts": acceptance.get("intent_source_counts", {}),
        "observation_count": len(observations) if isinstance(observations, list) else 0,
        "evidence_count": len(evidence) if isinstance(evidence, list) else 0,
        "error_count": len(errors) if isinstance(errors, list) else 0,
        "error_details": normalized_errors,
        "mandatory_source_blocked": mandatory_source_blocked,
        "runtime_terminal_status_version": checkpoint.get("final_terminal_status_version"),
        "runtime_coverage_version": checkpoint.get("final_intent_coverage_version"),
        "runtime_fully_covered": checkpoint.get("final_fully_covered"),
        "runtime_covered_intents": checkpoint.get("final_covered_intents", []),
        "runtime_uncovered_intents": checkpoint.get("final_uncovered_intents", []),
        "runtime_intent_source_counts": checkpoint.get("final_intent_source_counts", {}),
        "execution_budget_version": checkpoint.get("execution_budget_version"),
        "execution_budget": checkpoint.get("execution_budget", {}),
        "source_block_circuit_breakers": checkpoint.get("source_block_circuit_breakers", {}),
        "research_queue_priority_version": checkpoint.get("research_queue_priority_version"),
        "research_queue_candidate_count": checkpoint.get("research_queue_candidate_count"),
        "research_queue_next": checkpoint.get("research_queue_next", []),
        "planner_notes": checkpoint.get("planner_notes", []),
        "discovery_queries": checkpoint.get("discovery_queries", []),
        "research_supervisor": checkpoint.get("research_supervisor", {}),
        "adaptive_followup_queries": checkpoint.get("adaptive_followup_queries", []),
        "historical_queries": checkpoint.get("curated_historical_queries", []),
        "public_map_queries": checkpoint.get("curated_public_map_queries", []),
        "entity_hypothesis_queries": checkpoint.get("llm_entity_hypothesis_queries", []),
        "query_shape_violations": query_shape_violations(checkpoint),
    }


async def _run_profile(profile_id: str) -> tuple[dict[str, object], list[str]]:
    output_dir = Path(".argus/probes/perm-ai-acceptance")
    output_dir.mkdir(parents=True, exist_ok=True)
    profiles = web_test_profiles()
    profile = profiles[profile_id]
    request = build_profile_request(
        profile_id,
        profile,
        city=CITY,
        address=ADDRESS,
    )
    settings = Settings(
        execution_role="embedded",
        storage_backend="sqlite",
        db_path=output_dir / f"{profile_id}.sqlite3",
        agent_enabled=True,
        llm_required=True,
    )
    report = await run_embedded_probe(
        settings,
        request,
        timeout_seconds=900.0,
        poll_interval_seconds=0.5,
    )
    (output_dir / f"{profile_id}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / f"{profile_id}.txt").write_text(
        render_probe_summary(report, preview_items=20, preview_chars=1000) + "\n",
        encoding="utf-8",
    )
    overview = _overview(profile_id, report)
    failures = acceptance_failures([overview])
    outcome = "source_blocked" if overview.get("mandatory_source_blocked") else "factual"
    summary = {
        "territory": {"city": CITY, "address": ADDRESS},
        "ai_required": True,
        "profile": profile_id,
        "accepted": not failures,
        "outcome": outcome,
        "failures": failures,
        "result": overview,
    }
    (output_dir / f"overview-{profile_id}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary, failures


async def main(profile_id: str | None = None) -> None:
    selected = (profile_id,) if profile_id else PROFILE_IDS
    summaries: list[dict[str, object]] = []
    all_failures: list[str] = []
    for current in selected:
        summary, failures = await _run_profile(current)
        summaries.append(summary)
        all_failures.extend(failures)

    payload = {
        "territory": {"city": CITY, "address": ADDRESS},
        "ai_required": True,
        "accepted": not all_failures,
        "failures": all_failures,
        "profiles": summaries,
    }
    output_dir = Path(".argus/probes/perm-ai-acceptance")
    (output_dir / "overview.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if all_failures:
        raise SystemExit("live Perm AI acceptance failed")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live ARGUS AI acceptance against a Perm address")
    parser.add_argument("--profile", choices=PROFILE_IDS, help="Run one independent consumer profile")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(args.profile))
