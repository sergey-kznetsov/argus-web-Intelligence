from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


@dataclass(slots=True, frozen=True)
class ImageReference:
    image_url: str
    declared_by: str
    alt: str | None = None
    title: str | None = None
    caption: str | None = None


@dataclass(slots=True)
class ImageReferenceExtraction:
    items: list[ImageReference] = field(default_factory=list)
    extractor_version: str = "html-image-reference/1"
    truncated: bool = False


def extract_image_references(
    html: str,
    *,
    content_type: str | None,
    base_url: str,
    max_scan_chars: int = 750_000,
    max_items: int = 50,
    max_value_chars: int = 3_000,
) -> ImageReferenceExtraction:
    """Extract bounded image references explicitly declared by one fetched HTML page.

    This function never downloads image bytes and never infers a place/date from pixels.
    The fetched page is the evidence that the image reference and accompanying metadata
    were source-declared.
    """

    if content_type and "html" not in content_type.casefold():
        return ImageReferenceExtraction()

    scan_limit = max(1, int(max_scan_chars))
    item_limit = max(1, int(max_items))
    value_limit = max(1, int(max_value_chars))
    source = html[:scan_limit]
    extraction = ImageReferenceExtraction(truncated=len(html) > scan_limit)
    soup = BeautifulSoup(source, "html.parser")
    seen: set[str] = set()

    def add(
        raw_url: object,
        *,
        declared_by: str,
        alt: object = None,
        title: object = None,
        caption: object = None,
    ) -> bool:
        url = _safe_url(base_url, raw_url)
        if url is None or url in seen:
            return False
        if len(extraction.items) >= item_limit:
            extraction.truncated = True
            return False
        seen.add(url)
        extraction.items.append(
            ImageReference(
                image_url=url,
                declared_by=declared_by,
                alt=_bounded_text(alt, value_limit),
                title=_bounded_text(title, value_limit),
                caption=_bounded_text(caption, value_limit),
            )
        )
        return True

    # Social/article metadata is usually the strongest source-declared representative image.
    for meta in soup.find_all("meta"):
        prop = str(meta.get("property") or "").strip().casefold()
        name = str(meta.get("name") or "").strip().casefold()
        key = prop or name
        if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
            add(meta.get("content"), declared_by=key)

    for image in soup.find_all("img"):
        raw_url = _first_image_url(image)
        if raw_url is None:
            continue
        figure = image.find_parent("figure")
        caption = None
        if figure is not None:
            figcaption = figure.find("figcaption")
            if figcaption is not None:
                caption = figcaption.get_text(" ", strip=True)
        add(
            raw_url,
            declared_by="img",
            alt=image.get("alt"),
            title=image.get("title"),
            caption=caption,
        )
        if len(extraction.items) >= item_limit:
            # There may be more images in the page; mark the bounded result explicitly.
            remaining = image.find_next("img")
            if remaining is not None:
                extraction.truncated = True
            break

    return extraction


def _first_image_url(tag) -> str | None:
    for attribute in ("src", "data-src", "data-original", "data-lazy-src"):
        value = tag.get(attribute)
        if isinstance(value, str) and value.strip():
            return value.strip()
    srcset = tag.get("srcset") or tag.get("data-srcset")
    if isinstance(srcset, str):
        first = srcset.split(",", 1)[0].strip().split(" ", 1)[0].strip()
        return first or None
    return None


def _safe_url(base_url: str, raw: object) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = urljoin(base_url, raw.strip())
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return candidate


def _bounded_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    return normalized[:limit] if normalized else None
