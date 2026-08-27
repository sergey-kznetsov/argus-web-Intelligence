from __future__ import annotations

from urllib.parse import parse_qs, urlsplit, urlunsplit


_PROVIDER_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("yandex_maps_web", ("yandex.ru", "yandex.com"), ("/maps",)),
    ("2gis_web", ("2gis.ru",), ("/",)),
    ("google_maps_web", ("google.com",), ("/maps",)),
)


def classify_public_map_url(url: str) -> dict[str, object] | None:
    """Classify known public map web surfaces from URL identity only.

    This function never claims that a page contains a card, review or rating. Those
    facts must come from extraction/Evidence. Host matching is boundary-aware so
    lookalike domains cannot acquire trusted provider provenance.
    """

    try:
        parsed = urlsplit(str(url).strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None

    host = parsed.hostname.rstrip(".").casefold()
    path = parsed.path or "/"
    for provider, domain_roots, path_prefixes in _PROVIDER_RULES:
        if not any(_host_matches(host, root) for root in domain_roots):
            continue
        if provider == "google_maps_web" and host == "maps.google.com":
            path_match = True
        else:
            path_match = any(_path_matches(path, prefix) for prefix in path_prefixes)
        if not path_match:
            continue
        return {
            "provider": provider,
            "access": "public_web_browser",
            "paid_api": False,
            "classification_basis": "url_host_path",
            "content_claimed": False,
        }
    return None


def public_map_surface_kind(url: str) -> str | None:
    """Return ``entity``, ``search`` or ``map`` for a known public map URL.

    Search-result surfaces are navigation, not attributable entity facts. Keeping this
    distinction URL-based lets extraction avoid promoting snippets, adverts or nearby results
    into Evidence for the requested address.
    """

    classification = classify_public_map_url(url)
    if classification is None:
        return None
    parsed = urlsplit(str(url).strip())
    segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
    provider = str(classification["provider"])

    if provider == "yandex_maps_web":
        if "org" in segments and any(segment.isdigit() for segment in segments):
            return "entity"
        query = parse_qs(parsed.query)
        if "search" in segments or "text" in query:
            return "search"
        return "map"

    if provider == "2gis_web":
        try:
            firm_index = segments.index("firm")
        except ValueError:
            firm_index = -1
        if firm_index >= 0 and firm_index + 1 < len(segments) and segments[firm_index + 1].isdigit():
            return "entity"
        if "search" in segments:
            return "search"
        return "map"

    if provider == "google_maps_web":
        if "place" in segments:
            return "entity"
        if "search" in segments:
            return "search"
        return "map"
    return "map"


def preferred_public_map_review_url(url: str) -> str | None:
    """Return a deterministic public review-view URL when the provider exposes one.

    Only URL shapes observed/documented as public browser surfaces are rewritten. The
    returned URL is still untrusted input and must pass the normal UrlGuard before use.
    Google Maps is intentionally not rewritten because a review-specific URI cannot be
    derived reliably from an arbitrary public place URL without additional place identity.
    """

    classification = classify_public_map_url(url)
    if classification is None:
        return None
    parsed = urlsplit(str(url).strip())
    segments = [segment for segment in parsed.path.split("/") if segment]
    provider = str(classification["provider"])

    target_path: str | None = None
    if provider == "yandex_maps_web":
        target_path = _yandex_review_path(segments)
    elif provider == "2gis_web":
        target_path = _two_gis_review_path(segments)
    if target_path is None:
        return None

    candidate = urlunsplit((parsed.scheme, parsed.netloc, target_path, "", ""))
    current = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return None if candidate.rstrip("/") == current.rstrip("/") else candidate


def _yandex_review_path(segments: list[str]) -> str | None:
    if len(segments) < 4 or segments[0] != "maps" or segments[1] != "org":
        return None
    organization_id_index = next(
        (index for index in range(2, len(segments)) if segments[index].isdigit()),
        None,
    )
    if organization_id_index is None:
        return None
    base = segments[: organization_id_index + 1]
    return "/" + "/".join([*base, "reviews"]) + "/"


def _two_gis_review_path(segments: list[str]) -> str | None:
    try:
        firm_index = segments.index("firm")
    except ValueError:
        return None
    if firm_index == 0 or firm_index + 1 >= len(segments):
        return None
    firm_id = segments[firm_index + 1]
    if not firm_id.isdigit():
        return None
    base = segments[: firm_index + 2]
    return "/" + "/".join([*base, "tab", "reviews"])


def _host_matches(host: str, root: str) -> bool:
    return host == root or host.endswith(f".{root}")


def _path_matches(path: str, prefix: str) -> bool:
    if prefix == "/":
        return path.startswith("/")
    return path == prefix or path.startswith(f"{prefix}/")
