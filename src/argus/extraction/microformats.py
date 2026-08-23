from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag


@dataclass(slots=True)
class MicroformatItem:
    kind: str
    properties: dict[str, list[object]]
    source_url: str
    entity_id: str
    title: str | None = None
    text: str | None = None
    published_at: datetime | None = None


@dataclass(slots=True)
class MicroformatsExtraction:
    items: list[MicroformatItem] = field(default_factory=list)
    roots_seen: int = 0
    roots_skipped: int = 0
    truncated: bool = False
    extractor_version: str = "microformats2-explicit/1"


_KIND_PROPERTIES = {
    "h-entry": (
        "p-name",
        "p-summary",
        "e-content",
        "dt-published",
        "dt-updated",
        "p-author",
        "p-category",
        "u-url",
        "u-uid",
        "u-syndication",
        "u-in-reply-to",
    ),
    "h-review": (
        "p-name",
        "p-item",
        "p-author",
        "dt-published",
        "p-rating",
        "p-best",
        "p-worst",
        "e-content",
        "p-category",
        "u-url",
    ),
}


def extract_microformats(
    html: str,
    *,
    content_type: str | None,
    base_url: str,
    max_scan_chars: int = 500_000,
    max_roots: int = 100,
    max_values_per_property: int = 20,
    max_value_chars: int = 10_000,
    max_html_chars: int = 25_000,
) -> MicroformatsExtraction:
    """Parse an explicit, bounded subset of h-entry and h-review Microformats2."""

    extraction = MicroformatsExtraction()
    if content_type and "html" not in content_type.casefold():
        return extraction
    scan_limit = max(1, int(max_scan_chars))
    root_limit = max(1, int(max_roots))
    value_limit = max(1, int(max_values_per_property))
    text_limit = max(1, int(max_value_chars))
    html_limit = max(1, int(max_html_chars))
    source = html[:scan_limit]
    extraction.truncated = len(html) > scan_limit
    soup = BeautifulSoup(source, "html.parser")

    roots: list[tuple[str, Tag]] = []
    for element in soup.find_all(class_=True):
        if not isinstance(element, Tag):
            continue
        classes = {str(value) for value in element.get("class", [])}
        for kind in _KIND_PROPERTIES:
            if kind in classes:
                roots.append((kind, element))
                break
    extraction.roots_seen = len(roots)
    if len(roots) > root_limit:
        extraction.truncated = True

    for kind, root in roots[:root_limit]:
        properties: dict[str, list[object]] = {}
        for property_class in _KIND_PROPERTIES[kind]:
            values: list[object] = []
            for element in root.find_all(class_=lambda value: _has_class(value, property_class)):
                if not isinstance(element, Tag) or not _belongs_to_root(element, root):
                    continue
                value = _property_value(
                    element,
                    property_class,
                    base_url=base_url,
                    max_value_chars=text_limit,
                    max_html_chars=html_limit,
                )
                if value is None or value in values:
                    continue
                values.append(value)
                if len(values) >= value_limit:
                    extraction.truncated = True
                    break
            if values:
                properties[property_class] = values

        if not properties:
            extraction.roots_skipped += 1
            continue
        item_url = _first_string(properties.get("u-url")) or base_url
        uid = _first_string(properties.get("u-uid")) or item_url
        title = _first_string(properties.get("p-name"))
        text = _content_text(properties.get("e-content"))
        if text is None:
            text = _first_string(properties.get("p-summary"))
        if kind == "h-review" and text is None:
            text = title
        published_at = _datetime(_first_string(properties.get("dt-published")))
        extraction.items.append(
            MicroformatItem(
                kind=kind,
                properties=properties,
                source_url=item_url,
                entity_id=uid,
                title=title,
                text=text,
                published_at=published_at,
            )
        )
    return extraction


def _has_class(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return expected in value.split()
    if isinstance(value, list):
        return expected in {str(item) for item in value}
    return False


def _belongs_to_root(element: Tag, root: Tag) -> bool:
    parent = element.parent
    while isinstance(parent, Tag) and parent is not root:
        classes = {str(value) for value in parent.get("class", [])}
        if any(value.startswith("h-") for value in classes):
            return False
        parent = parent.parent
    return parent is root


def _property_value(
    element: Tag,
    property_class: str,
    *,
    base_url: str,
    max_value_chars: int,
    max_html_chars: int,
) -> object | None:
    prefix = property_class[:2]
    if prefix == "u-":
        raw = (
            element.get("href")
            or element.get("src")
            or element.get("data")
            or element.get("value")
            or element.get_text(" ", strip=True)
        )
        return _safe_url(base_url, str(raw or ""))
    if prefix == "dt":
        raw = (
            element.get("datetime")
            or element.get("title")
            or element.get("value")
            or element.get_text(" ", strip=True)
        )
        return _bounded_text(raw, max_value_chars)
    if prefix == "e-":
        text = _bounded_text(element.get_text("\n", strip=True), max_value_chars)
        html = element.decode_contents()[:max_html_chars]
        if text is None and not html.strip():
            return None
        return {"value": text or "", "html": html}
    raw = (
        element.get("content")
        or element.get("value")
        or element.get("title")
        or element.get_text(" ", strip=True)
    )
    return _bounded_text(raw, max_value_chars)


def _bounded_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    clean = " ".join(str(value).split()).strip()
    return clean[:limit] if clean else None


def _safe_url(base_url: str, raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    candidate = urljoin(base_url, value)
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return candidate


def _first_string(values: list[object] | None) -> str | None:
    if not values:
        return None
    value = values[0]
    return value if isinstance(value, str) and value else None


def _content_text(values: list[object] | None) -> str | None:
    if not values:
        return None
    value = values[0]
    if isinstance(value, dict):
        text = value.get("value")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
