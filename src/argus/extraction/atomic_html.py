from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

_ATOMIC_SELECTOR_VERSION = "html-atomic/1"
_REMOVE_TAGS = ("script", "style", "noscript", "svg", "template", "form", "nav", "aside", "footer")


@dataclass(frozen=True, slots=True)
class AtomicHtmlBlock:
    title: str
    text: str
    selector_kind: str
    declared_time: str | None = None


@dataclass(frozen=True, slots=True)
class AtomicHtmlExtraction:
    items: tuple[AtomicHtmlBlock, ...]
    extractor_version: str = _ATOMIC_SELECTOR_VERSION
    truncated: bool = False


def extract_atomic_html_blocks(
    content: str,
    *,
    content_type: str | None,
    max_scan_chars: int = 750_000,
    max_items: int = 20,
    max_text_chars: int = 100_000,
) -> AtomicHtmlExtraction:
    """Extract only source-declared, self-contained publication containers.

    The extractor intentionally avoids CSS-class and source-contour heuristics. It accepts
    semantic HTML ``article`` elements, ARIA ``role=article`` containers and explicit
    ``itemprop=articleBody`` containers. These source-declared structures are suitable as a
    deterministic fallback when schema.org/microformats did not expose an atomic entity.
    """

    if max_scan_chars <= 0 or max_items <= 0 or max_text_chars <= 0:
        return AtomicHtmlExtraction(items=())
    if content_type and "html" not in content_type.casefold():
        return AtomicHtmlExtraction(items=())

    source = content[:max_scan_chars]
    soup = BeautifulSoup(source, "html.parser")
    candidates = _candidate_tags(soup)
    items: list[AtomicHtmlBlock] = []
    seen_payloads: set[tuple[str, str]] = set()
    truncated = len(candidates) > max_items

    for tag, selector_kind in candidates[:max_items]:
        item = _extract_candidate(
            tag,
            selector_kind=selector_kind,
            max_text_chars=max_text_chars,
        )
        if item is None:
            continue
        key = (item.title.casefold(), item.text.casefold())
        if key in seen_payloads:
            continue
        seen_payloads.add(key)
        items.append(item)

    return AtomicHtmlExtraction(items=tuple(items), truncated=truncated)


def _candidate_tags(soup: BeautifulSoup) -> list[tuple[Tag, str]]:
    candidates: list[tuple[Tag, str]] = []
    seen: set[int] = set()

    for tag in soup.find_all("article"):
        if isinstance(tag, Tag) and id(tag) not in seen:
            seen.add(id(tag))
            candidates.append((tag, "article"))

    for tag in soup.find_all(attrs={"role": "article"}):
        if isinstance(tag, Tag) and id(tag) not in seen:
            seen.add(id(tag))
            candidates.append((tag, "role_article"))

    for tag in soup.find_all(attrs={"itemprop": True}):
        if not isinstance(tag, Tag) or id(tag) in seen:
            continue
        tokens = {token.casefold() for token in _attribute_tokens(tag.get("itemprop"))}
        if "articlebody" not in tokens:
            continue
        seen.add(id(tag))
        candidates.append((tag, "itemprop_articlebody"))

    return candidates


def _extract_candidate(
    tag: Tag,
    *,
    selector_kind: str,
    max_text_chars: int,
) -> AtomicHtmlBlock | None:
    clone = BeautifulSoup(str(tag), "html.parser")
    root = clone.find()
    if not isinstance(root, Tag):
        return None

    title_tag = root.find(["h1", "h2", "h3"])
    if not isinstance(title_tag, Tag):
        return None
    title = _normalize_inline(title_tag.get_text(" ", strip=True))[:500]
    if len(title) < 4:
        return None

    declared_time = _declared_time(root)
    for removable in root.find_all(_REMOVE_TAGS):
        removable.decompose()

    lines = [
        line.strip()
        for line in root.get_text("\n").splitlines()
        if line.strip()
    ]
    if lines and _normalize_inline(lines[0]).casefold() == title.casefold():
        lines = lines[1:]
    text = "\n".join(lines).strip()[:max_text_chars]
    if len(text) < 120:
        return None

    return AtomicHtmlBlock(
        title=title,
        text=text,
        selector_kind=selector_kind,
        declared_time=declared_time,
    )


def _declared_time(root: Tag) -> str | None:
    time_tag = root.find("time")
    if isinstance(time_tag, Tag):
        datetime_value = time_tag.get("datetime")
        if isinstance(datetime_value, str) and datetime_value.strip():
            return datetime_value.strip()[:128]
        text = _normalize_inline(time_tag.get_text(" ", strip=True))
        if text:
            return text[:128]

    for tag in root.find_all(attrs={"itemprop": True}):
        if not isinstance(tag, Tag):
            continue
        tokens = {token.casefold() for token in _attribute_tokens(tag.get("itemprop"))}
        if "datepublished" not in tokens:
            continue
        content = tag.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:128]
        text = _normalize_inline(tag.get_text(" ", strip=True))
        if text:
            return text[:128]
    return None


def _attribute_tokens(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(value.split())
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _normalize_inline(value: str) -> str:
    return " ".join(value.split())


__all__ = [
    "AtomicHtmlBlock",
    "AtomicHtmlExtraction",
    "extract_atomic_html_blocks",
]
