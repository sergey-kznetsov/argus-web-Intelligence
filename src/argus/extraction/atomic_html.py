from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup, Tag

_ATOMIC_SELECTOR_VERSION = "html-atomic/2"
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
    navigation_cards_suppressed: int = 0


def extract_atomic_html_blocks(
    content: str,
    *,
    content_type: str | None,
    base_url: str | None = None,
    max_scan_chars: int = 750_000,
    max_items: int = 20,
    max_text_chars: int = 100_000,
) -> AtomicHtmlExtraction:
    """Extract only source-declared, self-contained publication containers.

    The extractor intentionally avoids CSS-class and source-contour heuristics. It accepts
    semantic HTML ``article`` elements, ARIA ``role=article`` containers and explicit
    ``itemprop=articleBody`` containers. Repeated HTML5 ``article`` cards whose headings link
    to different destination URLs are treated as listing navigation, not publications; ARGUS
    should follow those links and extract the destination page instead.
    """

    if max_scan_chars <= 0 or max_items <= 0 or max_text_chars <= 0:
        return AtomicHtmlExtraction(items=())
    if content_type and "html" not in content_type.casefold():
        return AtomicHtmlExtraction(items=())

    source = content[:max_scan_chars]
    soup = BeautifulSoup(source, "html.parser")
    candidates = _candidate_tags(soup)
    listing_card_ids = _listing_article_card_ids(candidates, base_url=base_url)
    eligible = [
        (tag, selector_kind)
        for tag, selector_kind in candidates
        if id(tag) not in listing_card_ids
    ]
    items: list[AtomicHtmlBlock] = []
    seen_payloads: set[tuple[str, str]] = set()
    truncated = len(eligible) > max_items

    for tag, selector_kind in eligible[:max_items]:
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

    return AtomicHtmlExtraction(
        items=tuple(items),
        truncated=truncated,
        navigation_cards_suppressed=len(listing_card_ids),
    )


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


def _listing_article_card_ids(
    candidates: list[tuple[Tag, str]],
    *,
    base_url: str | None,
) -> set[int]:
    """Identify repeated linked-heading HTML5 article cards without semantic guessing."""

    linked: list[tuple[Tag, str]] = []
    for tag, selector_kind in candidates:
        if selector_kind != "article":
            continue
        target = _linked_heading_target(tag, base_url=base_url)
        if target is not None:
            linked.append((tag, target))
    if len({target for _, target in linked}) < 2:
        return set()
    return {id(tag) for tag, _ in linked}


def _linked_heading_target(tag: Tag, *, base_url: str | None) -> str | None:
    heading = tag.find(["h1", "h2", "h3"])
    if not isinstance(heading, Tag):
        return None
    link = heading.find("a", href=True)
    if not isinstance(link, Tag):
        parent = heading.parent
        link = parent if isinstance(parent, Tag) and parent.name == "a" and parent.get("href") else None
    if not isinstance(link, Tag):
        return None
    href = link.get("href")
    if not isinstance(href, str) or not href.strip():
        return None
    target = urldefrag(urljoin(base_url or "", href.strip()))[0]
    if not target:
        return None
    if base_url and target.rstrip("/") == urldefrag(base_url)[0].rstrip("/"):
        return None
    return target


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
