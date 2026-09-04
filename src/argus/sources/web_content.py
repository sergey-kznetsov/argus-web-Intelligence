from __future__ import annotations

from bs4 import BeautifulSoup, Tag


_ALWAYS_REMOVE = (
    "script",
    "style",
    "noscript",
    "svg",
    "template",
)

_BOILERPLATE_TAGS = (
    "form",
    "nav",
    "header",
    "footer",
    "aside",
    "dialog",
)

_BOILERPLATE_ROLES = (
    "banner",
    "contentinfo",
    "navigation",
    "search",
)


def extract_readable_text(content: str, content_type: str | None) -> str:
    """Extract user-facing page content without login/navigation boilerplate.

    Structured-data extractors still receive the untouched HTML elsewhere in the ARGUS
    pipeline. This function is only responsible for the generic textual document Evidence.
    Prefer semantic HTML containers when present, then fall back to a cleaned body.
    """

    if content_type and "html" not in content_type.casefold():
        return content

    soup = BeautifulSoup(content, "html.parser")
    for tag in soup.find_all(_ALWAYS_REMOVE):
        tag.decompose()

    root = _preferred_root(soup)
    _strip_boilerplate(root)
    text = _normalized_text(root)

    if len(text) >= 80 or root is soup.body or soup.body is None:
        return text

    # Some sites use an almost-empty <main> while real content sits in the body. A bounded
    # fallback keeps extraction useful without restoring forms/navigation that were removed.
    fallback = BeautifulSoup(content, "html.parser")
    for tag in fallback.find_all(_ALWAYS_REMOVE):
        tag.decompose()
    body = fallback.body or fallback
    _strip_boilerplate(body)
    return _normalized_text(body)


def _preferred_root(soup: BeautifulSoup) -> Tag | BeautifulSoup:
    article = soup.find("article")
    if isinstance(article, Tag):
        return article
    main = soup.find("main")
    if isinstance(main, Tag):
        return main
    return soup.body or soup


def _strip_boilerplate(root: Tag | BeautifulSoup) -> None:
    for tag in root.find_all(_BOILERPLATE_TAGS):
        tag.decompose()
    for role in _BOILERPLATE_ROLES:
        for tag in root.find_all(attrs={"role": role}):
            tag.decompose()


def _normalized_text(root: Tag | BeautifulSoup) -> str:
    return "\n".join(
        line.strip()
        for line in root.get_text("\n").splitlines()
        if line.strip()
    )


__all__ = ["extract_readable_text"]
