from __future__ import annotations

from pathlib import Path

from argus.contracts.models import CollectionRequest, Observation
from argus.research.historical_catalog import HistoricalSourceCatalog, HistoricalSourceProfile


RUSSIA_USSR_HISTORICAL_SOURCES: tuple[HistoricalSourceProfile, ...] = (
    HistoricalSourceProfile("pastvu", "pastvu.com", "historical_photos", 10, visual=True),
    HistoricalSourceProfile("etomesto", "etomesto.ru", "historical_maps", 20, visual=True),
    HistoricalSourceProfile("retromap", "retromap.ru", "historical_maps", 30, visual=True),
    HistoricalSourceProfile("rgakfd", "photo.rgakfd.ru", "photo_archive", 40, visual=True),
    HistoricalSourceProfile("presidential_library", "prlib.ru", "archive_library", 50, visual=True),
    HistoricalSourceProfile("neb", "rusneb.ru", "digital_library", 60, visual=True),
    HistoricalSourceProfile("rosarchive", "archives.gov.ru", "archive_catalogues", 70),
    HistoricalSourceProfile("runivers", "runivers.ru", "historical_library", 80, visual=True),
    HistoricalSourceProfile("loc_prokudin_gorskii", "loc.gov", "historical_photos", 90, visual=True),
    HistoricalSourceProfile("prozhito", "prozhito.org", "historical_context", 100),
)


class HistoricalSourceResearchPlanner:
    """Generate bounded discovery queries from built-in and operator-added historical pools."""

    version = "russia-ussr-historical-sources/2"

    def __init__(
        self,
        sources: tuple[HistoricalSourceProfile, ...] | None = None,
        *,
        catalog_file: Path | None = None,
        max_anchor_chars: int = 180,
    ) -> None:
        if sources is None:
            catalog = HistoricalSourceCatalog(RUSSIA_USSR_HISTORICAL_SOURCES)
            sources = catalog.profiles(catalog_file)
            self.catalog_version = catalog.version
        else:
            self.catalog_version = "explicit-source-list"
        self.sources = tuple(sorted(sources, key=lambda item: (item.priority, item.source_id)))
        self.max_anchor_chars = max(32, int(max_anchor_chars))

    def queries(
        self,
        request: CollectionRequest,
        *,
        observations: list[Observation] | None = None,
        seen_queries: set[str] | None = None,
        limit: int = 8,
    ) -> list[str]:
        if "historical_context" not in request.intents or limit <= 0:
            return []
        seen = {value.casefold() for value in (seen_queries or set())}
        anchors = self._anchors(request, observations or [])
        if not anchors:
            return []

        result: list[str] = []
        # First pass gives every high-priority source the current territory. Subsequent
        # passes use newly discovered historical entity names when budget remains.
        for anchor in anchors:
            for profile in self.sources:
                query = self._query(profile, anchor)
                key = query.casefold()
                if key in seen:
                    continue
                seen.add(key)
                result.append(query)
                if len(result) >= limit:
                    return result
        return result

    def source_metadata(self) -> list[dict[str, object]]:
        return [
            {
                "source_id": item.source_id,
                "domain": item.domain,
                "kind": item.kind,
                "priority": item.priority,
                "visual": item.visual,
                "query_suffix": item.query_suffix,
                "origin": item.origin,
                "catalog_version": self.catalog_version,
                "catalog_entry_is_evidence": False,
            }
            for item in self.sources
        ]

    def _anchors(
        self,
        request: CollectionRequest,
        observations: list[Observation],
    ) -> list[str]:
        values: list[str] = []
        territory = self._territory_text(request)
        if territory:
            values.append(territory)
        for observation in observations:
            for raw in (
                observation.title,
                observation.data.get("name"),
                observation.data.get("former_name"),
                observation.data.get("old_name"),
                observation.data.get("operator"),
                observation.data.get("brand"),
            ):
                if not isinstance(raw, str):
                    continue
                value = self._clean_anchor(raw)
                if value and value.casefold() not in {item.casefold() for item in values}:
                    values.append(value)
            if len(values) >= 12:
                break
        return values

    def _query(self, profile: HistoricalSourceProfile, anchor: str) -> str:
        suffix = profile.query_suffix
        if not suffix:
            if profile.kind in {"historical_photos", "photo_archive"}:
                suffix = "фото фотография"
            elif profile.kind == "historical_maps":
                suffix = "карта план"
        suffix_text = f" {suffix}" if suffix else ""
        return f'site:{profile.domain} "{anchor}"{suffix_text}'[:512].rstrip()

    def _clean_anchor(self, value: str) -> str | None:
        clean = " ".join(value.replace('"', " ").replace("\\", " ").split()).strip()
        if not clean or len(clean) < 3:
            return None
        return clean[: self.max_anchor_chars].rstrip()

    @staticmethod
    def _territory_text(request: CollectionRequest) -> str:
        city = (request.territory.city or "").strip()
        address = (request.territory.address or "").strip()
        if city and address:
            return address if city.casefold() in address.casefold() else f"{city}, {address}"
        if address:
            return address
        if city:
            return city
        if request.territory.point:
            return (
                f"{request.territory.point.latitude:.6f},"
                f"{request.territory.point.longitude:.6f}"
            )
        return ""
