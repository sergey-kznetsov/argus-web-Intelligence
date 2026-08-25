from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag


@dataclass(slots=True)
class MicrodataItem:
    index: int
    item_types: list[str]
    item_id: str | None
    properties: dict[str, list[object]]
    entity_id: str
    title: str | None = None
    text: str | None = None
    published_at: datetime | None = None
    truncated: bool = False


@dataclass(slots=True)
class MicrodataExtraction:
    items: list[MicrodataItem] = field(default_factory=list)
    items_seen: int = 0
    items_skipped: int = 0
    itemref_skipped: int = 0
    truncated: bool = False
    extractor_version: str = "html-microdata-explicit/2"


def extract_microdata(
    html: str,
    *,
    content_type: str | None,
    base_url: str,
    max_scan_chars: int = 750_000,
    max_items: int = 100,
    max_properties_per_item: int = 100,
    max_values_per_property: int = 20,
    max_value_chars: int = 10_000,
    max_nested_depth: int = 2,
) -> MicrodataExtraction:
    """Extract a bounded explicit subset of HTML Microdata.

    The extractor follows source-declared ``itemscope``/``itemprop`` values only.
    ``itemref`` top-level items are skipped rather than partially represented.
    Nested item properties are retained with explicit depth/property/value bounds so
    relationships such as Review -> Rating remain factual without DOM inference.
    Any clipping or unsupported value marks the affected item/extraction as truncated.
    """

    extraction = MicrodataExtraction()
    if content_type and "html" not in content_type.casefold():
        return extraction

    scan_limit = max(1, int(max_scan_chars))
    item_limit = max(1, int(max_items))
    property_limit = max(1, int(max_properties_per_item))
    value_limit = max(1, int(max_values_per_property))
    text_limit = max(1, int(max_value_chars))
    nested_depth_limit = max(0, min(int(max_nested_depth), 8))
    source = html[:scan_limit]
    extraction.truncated = len(html) > scan_limit
    soup = BeautifulSoup(source, "html.parser")
    scopes = [
        element
        for element in soup.find_all(attrs={"itemscope": True})
        if isinstance(element, Tag)
    ]
    extraction.items_seen = len(scopes)
    if len(scopes) > item_limit:
        extraction.truncated = True

    for index, scope in enumerate(scopes[:item_limit]):
        if str(scope.get("itemref", "")).strip():
            extraction.itemref_skipped += 1
            extraction.items_skipped += 1
            continue

        item_types, item_types_truncated = _tokens(
            scope.get("itemtype"),
            max_tokens=10,
            max_chars=2_000,
        )
        item_id, item_id_truncated = _identifier_value(
            base_url,
            scope.get("itemid"),
            text_limit,
        )
        properties, properties_truncated = _scope_properties(
            scope,
            base_url=base_url,
            max_properties=property_limit,
            max_values_per_property=value_limit,
            max_value_chars=text_limit,
            nested_depth=0,
            max_nested_depth=nested_depth_limit,
        )
        item_truncated = item_types_truncated or item_id_truncated or properties_truncated
        if item_truncated:
            extraction.truncated = True

        if not item_types and item_id is None and not properties:
            extraction.items_skipped += 1
            continue

        title = _first_string(properties, "name", "headline")
        text = _first_string(properties, "description", "abstract")
        published_at = _datetime(_first_string(properties, "datePublished"))
        entity_id = item_id or f"microdata:{index}"
        extraction.items.append(
            MicrodataItem(
                index=index,
                item_types=item_types,
                item_id=item_id,
                properties=properties,
                entity_id=entity_id,
                title=title,
                text=text,
                published_at=published_at,
                truncated=item_truncated,
            )
        )
    return extraction


def _scope_properties(
    scope: Tag,
    *,
    base_url: str,
    max_properties: int,
    max_values_per_property: int,
    max_value_chars: int,
    nested_depth: int,
    max_nested_depth: int,
) -> tuple[dict[str, list[object]], bool]:
    properties: dict[str, list[object]] = {}
    truncated = False
    property_names_seen = 0

    for element in scope.find_all(attrs={"itemprop": True}):
        if not isinstance(element, Tag) or not _belongs_to_scope(element, scope):
            continue
        names, names_truncated = _tokens(
            element.get("itemprop"),
            max_tokens=20,
            max_chars=128,
        )
        truncated = truncated or names_truncated
        if not names:
            continue
        value, value_truncated = _property_value(
            element,
            owner_scope=scope,
            base_url=base_url,
            max_value_chars=max_value_chars,
            max_nested_properties=max_properties,
            max_nested_values=max_values_per_property,
            nested_depth=nested_depth,
            max_nested_depth=max_nested_depth,
        )
        truncated = truncated or value_truncated
        if value is None:
            continue
        for name in names:
            if name not in properties:
                if property_names_seen >= max_properties:
                    truncated = True
                    break
                properties[name] = []
                property_names_seen += 1
            values = properties[name]
            if value in values:
                continue
            if len(values) >= max_values_per_property:
                truncated = True
                continue
            values.append(value)
        if property_names_seen >= max_properties and any(
            name not in properties for name in names
        ):
            break

    return {name: values for name, values in properties.items() if values}, truncated


def _belongs_to_scope(element: Tag, scope: Tag) -> bool:
    parent_scope = element.find_parent(attrs={"itemscope": True})
    return parent_scope is scope


def _tokens(
    value: object,
    *,
    max_tokens: int,
    max_chars: int,
) -> tuple[list[str], bool]:
    if value is None:
        return [], False
    raw_values: list[str]
    if isinstance(value, list):
        raw_values = [str(item) for item in value]
    else:
        raw_values = [str(value)]
    tokens: list[str] = []
    truncated = False
    for raw in raw_values:
        for token in raw.split():
            clean = token.strip()
            if not clean:
                continue
            if len(clean) > max_chars:
                truncated = True
                continue
            if clean in tokens:
                continue
            if len(tokens) >= max_tokens:
                return tokens, True
            tokens.append(clean)
    return tokens, truncated


def _property_value(
    element: Tag,
    *,
    owner_scope: Tag,
    base_url: str,
    max_value_chars: int,
    max_nested_properties: int,
    max_nested_values: int,
    nested_depth: int,
    max_nested_depth: int,
) -> tuple[object | None, bool]:
    if element.has_attr("itemscope"):
        return _item_reference(
            element,
            base_url,
            max_value_chars,
            max_properties=max_nested_properties,
            max_values_per_property=max_nested_values,
            nested_depth=nested_depth + 1,
            max_nested_depth=max_nested_depth,
        )

    name = element.name.casefold()
    if name == "meta":
        return _bounded_text(element.get("content", ""), max_value_chars)
    if name in {"audio", "embed", "iframe", "img", "source", "track", "video"}:
        return _url_value(base_url, element.get("src"), max_value_chars)
    if name in {"a", "area", "link"}:
        return _url_value(base_url, element.get("href"), max_value_chars)
    if name == "object":
        return _url_value(base_url, element.get("data"), max_value_chars)
    if name in {"data", "meter"}:
        return _bounded_text(element.get("value", ""), max_value_chars)
    if name == "time":
        raw = element.get("datetime")
        if raw is not None:
            return _bounded_text(raw, max_value_chars)
    return _owned_text(element, owner_scope, max_value_chars)


def _item_reference(
    element: Tag,
    base_url: str,
    limit: int,
    *,
    max_properties: int,
    max_values_per_property: int,
    nested_depth: int,
    max_nested_depth: int,
) -> tuple[dict[str, object] | None, bool]:
    item_types, item_types_truncated = _tokens(
        element.get("itemtype"),
        max_tokens=10,
        max_chars=2_000,
    )
    item_id, item_id_truncated = _identifier_value(base_url, element.get("itemid"), limit)
    truncated = item_types_truncated or item_id_truncated
    result: dict[str, object] = {
        "itemid": item_id,
        "itemtype": item_types,
    }

    if str(element.get("itemref", "")).strip():
        return (result if item_types or item_id is not None else None), True
    if nested_depth > max_nested_depth:
        return (result if item_types or item_id is not None else None), True

    properties, properties_truncated = _scope_properties(
        element,
        base_url=base_url,
        max_properties=max_properties,
        max_values_per_property=max_values_per_property,
        max_value_chars=limit,
        nested_depth=nested_depth,
        max_nested_depth=max_nested_depth,
    )
    truncated = truncated or properties_truncated
    if properties:
        result["properties"] = properties
    if not item_types and item_id is None and not properties:
        return None, truncated
    return result, truncated


def _owned_text(
    element: Tag,
    owner_scope: Tag,
    limit: int,
) -> tuple[str | None, bool]:
    parts: list[str] = []
    for node in element.descendants:
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent if isinstance(node.parent, Tag) else None
        nearest_scope = (
            parent.find_parent(attrs={"itemscope": True}) if parent is not None else None
        )
        if nearest_scope is owner_scope:
            clean = " ".join(str(node).split()).strip()
            if clean:
                parts.append(clean)
    return _bounded_text(" ".join(parts), limit)


def _identifier_value(
    base_url: str,
    value: object,
    limit: int,
) -> tuple[str | None, bool]:
    raw, truncated = _bounded_text(value, limit)
    if raw is None:
        return None, truncated
    if truncated:
        return None, True
    candidate = _url_or_identifier(base_url, raw, require_safe_scheme=False)
    return candidate, candidate is None


def _url_value(
    base_url: str,
    value: object,
    limit: int,
) -> tuple[str | None, bool]:
    raw, truncated = _bounded_text(value, limit)
    if raw is None:
        return None, truncated
    if truncated:
        return None, True
    candidate = _url_or_identifier(base_url, raw, require_safe_scheme=True)
    return candidate, candidate is None


def _url_or_identifier(
    base_url: str,
    raw: str,
    *,
    require_safe_scheme: bool,
) -> str | None:
    candidate = urljoin(base_url, raw)
    parsed = urlsplit(candidate)
    if parsed.scheme in {"http", "https"}:
        if not parsed.hostname or parsed.username or parsed.password:
            return None
        return candidate
    parsed_raw = urlsplit(raw)
    if parsed_raw.scheme in {"mailto", "tel", "urn"}:
        return raw
    if require_safe_scheme:
        return None
    if parsed_raw.scheme:
        return None
    return raw if raw else None


def _bounded_text(value: object, limit: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    clean = " ".join(str(value).split()).strip()
    if not clean:
        return None, False
    return clean[:limit], len(clean) > limit


def _first_string(properties: dict[str, list[object]], *names: str) -> str | None:
    for name in names:
        values = properties.get(name, [])
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
