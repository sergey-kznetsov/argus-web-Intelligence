from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from argus.cli.probe import render_probe_summary, run_embedded_probe
from argus.config import Settings
from argus.contracts.models import CollectionConstraints, CollectionRequest, TerritoryContext
from argus.web.profiles import web_test_profiles

ADDRESS = "Комсомольский проспект, 27"
CITY = "Пермь"
PROFILE_IDS = ("kraken", "janus", "historical")
ACCEPTABLE_STATUSES = {"completed", "partial"}
_QUERY_CHECKPOINT_KEYS = (
    "queries",
    "discovery_queries",
    "adaptive_followup_queries",
    "curated_historical_queries",
    "curated_public_map_queries",
    "llm_entity_hypothesis_queries",
)


def _request(profile_id: str, profile: dict[str, object]) -> CollectionRequest:
    intents = [str(value) for value in profile.get("intents", []) if str(value).strip()]
    return CollectionRequest(
        consumer=str(profile["consumer"]),
        analysis_id=f"perm-ai-acceptance-{profile_id}",
        territory=TerritoryContext(city=CITY, address=ADDRESS),
        intents=intents,
        constraints=CollectionConstraints(
            max_pages=min(int(profile.get("max_pages", 18)), 18),
            max_depth=min(int(profile.get("max_depth", 2)), 3),
            max_duration_seconds=720.0,
            output_language="ru",
        ),
        allow_partial=True,
    )


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
    error_details = _error_details(errors)
    mandatory_source_blocked = _mandatory_janus_source_blocked(
        profile_id=profile_id,
        status=str(result.get("status") or ""),
        observation_count=len(observations) if isinstance(observations, list) else 0,
        evidence_count=len(evidence) if isinstance(evidence, list) else 0,
        error_details=error_details,
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
        "error_details": error_details,
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
        "query_shape_violations": _query_shape_violations(checkpoint),
    }


def _acceptance_failures(overview: list[dict[str, object]]) -> list[str]:
    failures: list[str] = []
    for item in overview:
        profile = str(item.get("profile") or "unknown")
        status = str(item.get("status") or "")
        expected_source_block = bool(item.get("mandatory_source_blocked"))
        covered = {
            str(value).strip().casefold()
            for value in item.get("covered_intents", [])
            if str(value).strip()
        }
        uncovered = {
            str(value).strip().casefold()
            for value in item.get("uncovered_intents", [])
            if str(value).strip()
        }
        if status not in ACCEPTABLE_STATUSES and not expected_source_block:
            failures.append(f"{profile}: terminal status is {status or 'missing'}")
        if int(item.get("observation_count") or 0) <= 0 and not expected_source_block:
            failures.append(f"{profile}: no factual observations")
        if int(item.get("evidence_count") or 0) <= 0 and not expected_source_block:
            failures.append(f"{profile}: no evidence")

        runtime_version = str(item.get("runtime_terminal_status_version") or "").strip()
        if runtime_version:
            runtime_covered = {
                str(value).strip().casefold()
                for value in item.get("runtime_covered_intents", [])
                if str(value).strip()
            }
            runtime_uncovered = {
                str(value).strip().casefold()
                for value in item.get("runtime_uncovered_intents", [])
                if str(value).strip()
            }
            if runtime_covered != covered or runtime_uncovered != uncovered:
                failures.append(
                    f"{profile}: production runtime coverage disagrees with independent probe"
                )

        query_violations = item.get("query_shape_violations")
        if isinstance(query_violations, list) and query_violations:
            failures.append(
                f"{profile}: malformed/service-shaped discovery query escaped sanitization"
            )

        supervisor = item.get("research_supervisor")
        if uncovered and not expected_source_block and not isinstance(supervisor, dict):
            failures.append(f"{profile}: factual gaps remained but research supervisor never ran")
        elif uncovered and not expected_source_block and not supervisor:
            failures.append(f"{profile}: factual gaps remained but research supervisor never ran")

        if profile == "kraken":
            if "reviews" not in covered:
                failures.append("kraken: reviews are not factually covered")
            secondary = {"public_mentions", "comments", "discussions", "local_news"}
            if not covered.intersection(secondary):
                failures.append("kraken: no secondary urban-information intent is covered")
        elif profile == "janus":
            required = {"residential_population", "residential_premises_count"}
            missing = sorted(required - covered)
            if missing and not expected_source_block:
                failures.append(
                    "janus: required residential intents are not factually covered: "
                    + ", ".join(missing)
                )
        elif profile == "historical" and "historical_context" not in covered:
            failures.append("historical: historical_context is not factually covered")
    return failures


def _error_details(errors: object) -> list[dict[str, str]]:
    if not isinstance(errors, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in errors:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or "").strip()
        source_id = str(raw.get("source_id") or "").strip()
        if not code:
            continue
        key = (code, source_id)
        if key in seen:
            continue
        seen.add(key)
        result.append({"code": code, "source_id": source_id})
    return result


def _mandatory_janus_source_blocked(
    *,
    profile_id: str,
    status: str,
    observation_count: int,
    evidence_count: int,
    error_details: list[dict[str, str]],
) -> bool:
    if profile_id != "janus" or status != "blocked":
        return False
    if observation_count != 0 or evidence_count != 0:
        return False
    return any(
        item.get("code") == "MINGKH_ACCESS_CHALLENGE"
        and item.get("source_id") == "mingkh_residential"
        for item in error_details
    )


def _query_shape_violations(checkpoint: dict[str, object]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for key in _QUERY_CHECKPOINT_KEYS:
        raw = checkpoint.get(key)
        if not isinstance(raw, list):
            continue
        for value in raw:
            query = " ".join(str(value).split()).strip()
            lowered = query.casefold()
            if not query:
                continue
            reason = ""
            if query[:1] in {"{", "["}:
                reason = "serialized_container"
            elif any(
                marker in lowered
                for marker in (
                    "metadata:",
                    "'metadata'",
                    '"metadata"',
                    "'queries'",
                    '"queries"',
                    "'search_string'",
                    '"search_string"',
                )
            ):
                reason = "service_schema_leak"
            if reason:
                violations.append({"checkpoint": key, "query": query[:512], "reason": reason})
    return violations


async def _run_profile(profile_id: str) -> tuple[dict[str, object], list[str]]:
    output_dir = Path(".argus/probes/perm-ai-acceptance")
    output_dir.mkdir(parents=True, exist_ok=True)
    profiles = web_test_profiles()
    profile = profiles[profile_id]
    request = _request(profile_id, profile)
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
    failures = _acceptance_failures([overview])
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
