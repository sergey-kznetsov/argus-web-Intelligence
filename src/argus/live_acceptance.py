from __future__ import annotations

from argus.contracts.models import CollectionConstraints, CollectionRequest, TerritoryContext

ACCEPTABLE_STATUSES = frozenset({"completed", "partial"})
JANUS_SOURCE_BLOCK_CODES = frozenset(
    {
        "MINGKH_ACCESS_CHALLENGE",
        "SOURCE_ROBOTS_ACCESS_BLOCKED",
        "SOURCE_ROBOTS_UNREACHABLE",
    }
)
QUERY_CHECKPOINT_KEYS = (
    "queries",
    "discovery_queries",
    "adaptive_followup_queries",
    "curated_historical_queries",
    "curated_public_map_queries",
    "llm_entity_hypothesis_queries",
)


def build_profile_request(
    profile_id: str,
    profile: dict[str, object],
    *,
    city: str,
    address: str,
) -> CollectionRequest:
    """Build the bounded request used by live consumer-profile acceptance probes."""

    intents = [str(value) for value in profile.get("intents", []) if str(value).strip()]
    return CollectionRequest(
        consumer=str(profile["consumer"]),
        analysis_id=f"perm-ai-acceptance-{profile_id}",
        territory=TerritoryContext(city=city, address=address),
        intents=intents,
        constraints=CollectionConstraints(
            max_pages=min(int(profile.get("max_pages", 18)), 18),
            max_depth=min(int(profile.get("max_depth", 2)), 3),
            max_duration_seconds=720.0,
            output_language="ru",
        ),
        allow_partial=True,
    )


def acceptance_failures(overview: list[dict[str, object]]) -> list[str]:
    """Return factual acceptance violations for independent consumer-profile probes."""

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
        if uncovered and not expected_source_block and (
            not isinstance(supervisor, dict) or not supervisor
        ):
            failures.append(f"{profile}: factual gaps remained but research supervisor never ran")

        if profile == "kraken":
            social_problem_intents = {
                "complaints",
                "public_appeals",
                "resident_messages",
                "incidents",
            }
            if not covered.intersection(social_problem_intents):
                failures.append("kraken: no social-problem intent is factually covered")
            supporting_intents = {"comments", "discussions", "posts", "local_news"}
            if not covered.intersection(supporting_intents):
                failures.append("kraken: no supporting urban-information intent is covered")
        elif profile == "janus":
            required = {"residential_population", "residential_premises_count"}
            missing = sorted(required - covered)
            if missing and not expected_source_block:
                failures.append(
                    "janus: required residential intents are not factually covered: "
                    + ", ".join(missing)
                )
        elif profile == "historical":
            required = {"historical_context", "historical_images", "public_mentions"}
            missing = sorted(required - covered)
            if missing:
                failures.append(
                    "historical: required intents are not factually covered: "
                    + ", ".join(missing)
                )

    return failures


def error_details(errors: object) -> list[dict[str, str]]:
    """Normalize unique source error handles used by acceptance policy."""

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


def mandatory_janus_source_blocked(
    *,
    profile_id: str,
    status: str,
    observation_count: int,
    evidence_count: int,
    error_details: list[dict[str, str]],
) -> bool:
    """Recognize an explicit external-source block without treating it as code failure."""

    if profile_id != "janus" or status != "blocked":
        return False
    if observation_count != 0 or evidence_count != 0:
        return False
    return any(
        item.get("code") in JANUS_SOURCE_BLOCK_CODES
        and item.get("source_id") in {"mingkh_residential", "site_discovery"}
        for item in error_details
    )


def query_shape_violations(checkpoint: dict[str, object]) -> list[dict[str, str]]:
    """Reject serialized service containers accidentally emitted as search queries."""

    violations: list[dict[str, str]] = []
    for key in QUERY_CHECKPOINT_KEYS:
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
