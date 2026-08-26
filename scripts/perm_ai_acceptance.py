from __future__ import annotations

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
        "research_supervisor": checkpoint.get("research_supervisor", {}),
        "adaptive_followup_queries": checkpoint.get("adaptive_followup_queries", []),
        "historical_queries": checkpoint.get("curated_historical_queries", []),
        "public_map_queries": checkpoint.get("curated_public_map_queries", []),
        "entity_hypothesis_queries": checkpoint.get("entity_hypothesis_queries", []),
    }


async def main() -> None:
    output_dir = Path(".argus/probes/perm-ai-acceptance")
    output_dir.mkdir(parents=True, exist_ok=True)
    profiles = web_test_profiles()
    overview: list[dict[str, object]] = []

    for profile_id in PROFILE_IDS:
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
            timeout_seconds=600.0,
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
        overview.append(_overview(profile_id, report))

    (output_dir / "overview.json").write_text(
        json.dumps(
            {
                "territory": {"city": CITY, "address": ADDRESS},
                "ai_required": True,
                "profiles": overview,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(overview, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
