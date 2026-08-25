from __future__ import annotations

from urllib.parse import urlsplit


_PROVIDER_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("yandex_maps_web", ("yandex.ru",), ("/maps",)),
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


def _host_matches(host: str, root: str) -> bool:
    return host == root or host.endswith(f".{root}")


def _path_matches(path: str, prefix: str) -> bool:
    if prefix == "/":
        return path.startswith("/")
    return path == prefix or path.startswith(f"{prefix}/")
