from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_KIND_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9][a-z0-9-]{0,62}$"
)
_MAX_CUSTOM_SOURCES = 200
_MAX_SUFFIX_CHARS = 80


@dataclass(frozen=True, slots=True)
class HistoricalSourceProfile:
    source_id: str
    domain: str
    kind: str
    priority: int
    visual: bool = False
    query_suffix: str = ""
    origin: str = "builtin"


class HistoricalSourceCatalogError(ValueError):
    pass


class HistoricalSourceCatalog:
    """Merge trusted code-shipped profiles with bounded operator-added source hints.

    A catalog entry only influences discovery queries. It never upgrades trust, bypasses
    URL/security policy, or becomes Evidence by being present in this catalog.
    """

    version = "historical-source-catalog/1"

    def __init__(self, builtin: tuple[HistoricalSourceProfile, ...]) -> None:
        self.builtin = tuple(builtin)

    def profiles(self, catalog_file: Path | None = None) -> tuple[HistoricalSourceProfile, ...]:
        custom = self._load_file(catalog_file) if catalog_file is not None else ()
        return self._merge(self.builtin, custom)

    def _load_file(self, path: Path) -> tuple[HistoricalSourceProfile, ...]:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise HistoricalSourceCatalogError(
                f"historical source catalog is not readable: {path}"
            ) from exc
        if len(raw.encode("utf-8")) > 512 * 1024:
            raise HistoricalSourceCatalogError("historical source catalog exceeds 512 KiB")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HistoricalSourceCatalogError("historical source catalog is invalid JSON") from exc
        if isinstance(payload, dict):
            values = payload.get("sources")
        else:
            values = payload
        if not isinstance(values, list):
            raise HistoricalSourceCatalogError("historical source catalog must contain a sources array")
        if len(values) > _MAX_CUSTOM_SOURCES:
            raise HistoricalSourceCatalogError(
                f"historical source catalog exceeds {_MAX_CUSTOM_SOURCES} custom sources"
            )
        return tuple(self._profile(item, index) for index, item in enumerate(values))

    @classmethod
    def _profile(cls, raw: object, index: int) -> HistoricalSourceProfile:
        if not isinstance(raw, dict):
            raise HistoricalSourceCatalogError(f"historical source #{index + 1} must be an object")
        source_id = str(raw.get("source_id") or "").strip().casefold()
        domain = cls._domain(str(raw.get("domain") or ""))
        kind = str(raw.get("kind") or "historical_context").strip().casefold()
        if not _SOURCE_ID_RE.fullmatch(source_id):
            raise HistoricalSourceCatalogError(f"historical source #{index + 1} has invalid source_id")
        if not _KIND_RE.fullmatch(kind):
            raise HistoricalSourceCatalogError(f"historical source #{index + 1} has invalid kind")
        try:
            priority = int(raw.get("priority", 500))
        except (TypeError, ValueError) as exc:
            raise HistoricalSourceCatalogError(
                f"historical source #{index + 1} has invalid priority"
            ) from exc
        if not 1 <= priority <= 10_000:
            raise HistoricalSourceCatalogError(
                f"historical source #{index + 1} priority must be 1..10000"
            )
        visual = raw.get("visual", False)
        if not isinstance(visual, bool):
            raise HistoricalSourceCatalogError(
                f"historical source #{index + 1} visual must be boolean"
            )
        suffix = " ".join(str(raw.get("query_suffix") or "").replace('"', " ").split())
        if len(suffix) > _MAX_SUFFIX_CHARS:
            raise HistoricalSourceCatalogError(
                f"historical source #{index + 1} query_suffix is too long"
            )
        return HistoricalSourceProfile(
            source_id=source_id,
            domain=domain,
            kind=kind,
            priority=priority,
            visual=visual,
            query_suffix=suffix,
            origin="operator_catalog",
        )

    @staticmethod
    def _domain(raw: str) -> str:
        value = raw.strip().casefold().strip(".")
        if "://" in value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise HistoricalSourceCatalogError("historical source domain URL is invalid")
            if parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment:
                raise HistoricalSourceCatalogError(
                    "historical source domain must not contain credentials, port, query or fragment"
                )
            if parsed.path not in {"", "/"}:
                raise HistoricalSourceCatalogError("historical source domain must not contain a path")
            value = parsed.hostname.casefold().strip(".")
        if not _DOMAIN_RE.fullmatch(value) or value in {"localhost", "example.com"}:
            raise HistoricalSourceCatalogError("historical source domain is invalid")
        return value

    @staticmethod
    def _merge(
        builtin: tuple[HistoricalSourceProfile, ...],
        custom: tuple[HistoricalSourceProfile, ...],
    ) -> tuple[HistoricalSourceProfile, ...]:
        by_id: dict[str, HistoricalSourceProfile] = {item.source_id: item for item in builtin}
        builtin_domains = {item.domain for item in builtin}
        for item in custom:
            # Operators may add candidates, not silently replace code-reviewed built-ins.
            if item.source_id in by_id or item.domain in builtin_domains:
                continue
            if any(existing.domain == item.domain for existing in by_id.values()):
                continue
            by_id[item.source_id] = item
        return tuple(sorted(by_id.values(), key=lambda item: (item.priority, item.source_id)))
