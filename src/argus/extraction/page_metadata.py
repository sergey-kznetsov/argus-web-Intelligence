from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


@dataclass(slots=True)
class PageMetadataExtraction:
    fields: dict[str, object] = field(default_factory=dict)
    canonical_url: str | None = None
    published_at: datetime | None = None
    extractor_version: str = "html-meta/1"
    truncated: bool = False


_SINGLE_PROPERTY_KEYS = {
    "og:title": "og_title",
    "og:type": "og_type",
    "og:url": "og_url",
    "og:description": "og_description",
    "og:site_name": "og_site_name",
    "og:locale": "og_locale",
    "article:published_time": "article_published_time",
    "article:modified_time": "article_modified_time",
    "article:expiration_time": "article_expiration_time",
    "article:section": "article_section",
}
_MULTI_PROPERTY_KEYS = {
    "article:author": "article_authors",
    "article:tag": "article_tags",
}
_SINGLE_NAME_KEYS = {
    "description": "description",
    "dc.title": "dc_title",
    "dcterms.title": "dcterms_title",
    "dc.date": "dc_date",
    "dcterms.date": "dcterms_date",
    "dcterms.created": "dcterms_created",
    "dcterms.modified": "dcterms_modified",
    "dc.description": "dc_description",
    "dcterms.description": "dcterms_description",
}
_MULTI_NAME_KEYS = {
    "author": "authors",
    "dc.creator": "dc_creators",
    "dcterms.creator": "dcterms_creators",
}


def extract_page_metadata(
    html: str,
    *,
    content_type: str | None,
    base_url: str,
    max_scan_chars: int = 500_000,
    max_value_chars: int = 5_000,
    max_values_per_field: int = 20,
) -> PageMetadataExtraction:
    """Extract bounded, source-declared metadata from an HTML document head."""

    if content_type and "html" not in content_type.casefold():
        return PageMetadataExtraction()
    scan_limit = max(1, int(max_scan_chars))
    value_limit = max(1, int(max_value_chars))
    list_limit = max(1, int(max_values_per_field))
    source = html[:scan_limit]
    extraction = PageMetadataExtraction(truncated=len(html) > scan_limit)
    soup = BeautifulSoup(source, "html.parser")
    head = soup.head or soup

    for link in head.find_all("link", href=True):
        rel = {str(item).casefold() for item in link.get("rel", [])}
        if "canonical" not in rel:
            continue
        canonical = _safe_url(base_url, str(link.get("href", "")))
        if canonical is not None:
            extraction.canonical_url = canonical
            extraction.fields["canonical_url"] = canonical
            break

    for tag in head.find_all("meta"):
        content = _bounded_value(tag.get("content"), value_limit)
        if content is None:
            continue
        prop = str(tag.get("property", "")).strip().casefold()
        name = str(tag.get("name", "")).strip().casefold()
        if prop in _SINGLE_PROPERTY_KEYS:
            extraction.fields.setdefault(_SINGLE_PROPERTY_KEYS[prop], content)
        elif prop in _MULTI_PROPERTY_KEYS:
            _append_value(
                extraction.fields,
                _MULTI_PROPERTY_KEYS[prop],
                content,
                limit=list_limit,
            )
        if name in _SINGLE_NAME_KEYS:
            extraction.fields.setdefault(_SINGLE_NAME_KEYS[name], content)
        elif name in _MULTI_NAME_KEYS:
            _append_value(
                extraction.fields,
                _MULTI_NAME_KEYS[name],
                content,
                limit=list_limit,
            )

    raw_og_url = extraction.fields.get("og_url")
    if isinstance(raw_og_url, str):
        safe_og_url = _safe_url(base_url, raw_og_url)
        if safe_og_url is None:
            extraction.fields.pop("og_url", None)
        else:
            extraction.fields["og_url"] = safe_og_url

    raw_published = extraction.fields.get("article_published_time")
    if isinstance(raw_published, str):
        extraction.published_at = _datetime(raw_published)
    return extraction


def _bounded_value(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    clean = " ".join(value.split()).strip()
    return clean[:limit] if clean else None


def _append_value(
    fields: dict[str, object],
    key: str,
    value: str,
    *,
    limit: int,
) -> None:
    existing = fields.get(key)
    values = existing if isinstance(existing, list) else []
    if value not in values and len(values) < limit:
        values.append(value)
    if values:
        fields[key] = values


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


def _datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
