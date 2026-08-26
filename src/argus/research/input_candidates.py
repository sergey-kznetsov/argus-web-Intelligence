from __future__ import annotations

from collections.abc import Iterable

from argus.contracts.models import CollectionRequest

MAX_RESEARCH_INPUT_CANDIDATES = 8
MAX_RESEARCH_INPUT_CHARS = 512


def research_input_candidates(
    request: CollectionRequest,
    *,
    extra_values: Iterable[object] = (),
) -> list[str]:
    """Return bounded values that AGENT may type into public search/filter controls.

    These values come only from caller research context or ARGUS-generated navigation
    queries. They are navigation data, never Evidence, and prevent the LLM from inventing
    arbitrary text for a public form.
    """

    city = (request.territory.city or "").strip()
    address = (request.territory.address or "").strip()
    territory = ""
    if city and address:
        territory = address if city.casefold() in address.casefold() else f"{city}, {address}"
    else:
        territory = address or city

    values: list[object] = [*extra_values]
    if territory:
        values.append(territory)
    if address and address != territory:
        values.append(address)
    if city and city != territory:
        values.append(city)

    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        normalized = " ".join(str(raw).split()).strip()
        normalized = normalized[:MAX_RESEARCH_INPUT_CHARS].rstrip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= MAX_RESEARCH_INPUT_CANDIDATES:
            break
    return result
