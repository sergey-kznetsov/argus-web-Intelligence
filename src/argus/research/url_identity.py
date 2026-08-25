from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "yclid",
    "_openstat",
    "mc_cid",
    "mc_eid",
}
_TRACKING_PREFIXES = ("utm_",)


def canonicalize_discovery_url(url: str) -> str | None:
    """Return a conservative HTTP(S) identity for discovery dedupe/crawl.

    The normalizer intentionally avoids path rewriting and arbitrary query sorting.
    It removes only fragments, default ports and widely used tracking parameters.
    """

    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or parsed.hostname is None:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None

    hostname = parsed.hostname.rstrip(".").casefold()
    if not hostname:
        return None
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return None

    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host_for_netloc if port is None or default_port else f"{host_for_netloc}:{port}"

    query_pairs = []
    try:
        parsed_pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return None
    for key, value in parsed_pairs:
        lowered = key.casefold()
        if lowered in _TRACKING_KEYS or any(lowered.startswith(prefix) for prefix in _TRACKING_PREFIXES):
            continue
        query_pairs.append((key, value))

    path = parsed.path or "/"
    query = urlencode(query_pairs, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))
