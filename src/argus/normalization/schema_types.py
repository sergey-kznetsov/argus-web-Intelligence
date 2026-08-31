from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

_DEFAULT_ENTITY_TYPE = "structured_entity"

_REVIEW_TYPES = {
    "Review",
    "ClaimReview",
    "CriticReview",
    "EmployerReview",
    "MediaReview",
}
_POST_TYPES = {
    "BlogPosting",
    "DiscussionForumPosting",
    "SocialMediaPosting",
}
_PUBLICATION_TYPES = {
    "Article",
    "NewsArticle",
    "AdvertiserContentArticle",
    "Report",
    "ScholarlyArticle",
    "TechArticle",
}
_COMMENT_TYPES = {"Comment"}
_DATASET_TYPES = {"Dataset", "DataCatalog"}
_PERSON_TYPES = {"Person"}
_PLACE_TYPES = {"Place"}
_PRODUCT_TYPES = {"Product"}
_SERVICE_TYPES = {"Service"}


def classify_schema_entity(
    raw_types: object,
    *,
    context_hints: Iterable[str] = (),
) -> tuple[str, list[str]]:
    """Classify only source-declared schema.org types into ARGUS factual categories.

    Unknown vocabularies and unrecognized schema.org types remain
    ``structured_entity``. The function does not dereference contexts or perform
    inheritance lookup over the live schema.org vocabulary.
    """

    schema_context = any(_is_schema_context(value) for value in context_hints)
    recognized = _schema_local_types(raw_types, schema_context=schema_context)
    if not recognized:
        return _DEFAULT_ENTITY_TYPE, []

    names = set(recognized)
    if names & _REVIEW_TYPES:
        return "review", recognized
    if names & _COMMENT_TYPES:
        return "comment", recognized
    if names & _POST_TYPES:
        return "post", recognized
    if names & _PUBLICATION_TYPES:
        return "publication", recognized
    if names & _DATASET_TYPES:
        return "dataset", recognized
    if any(name == "Event" or name.endswith("Event") for name in names):
        return "event", recognized
    if any(name == "Organization" or name.endswith("Organization") for name in names):
        return "organization", recognized
    if names & _PERSON_TYPES:
        return "person", recognized
    if names & _PLACE_TYPES:
        return "place", recognized
    if names & _PRODUCT_TYPES:
        return "product", recognized
    if names & _SERVICE_TYPES:
        return "service", recognized
    return _DEFAULT_ENTITY_TYPE, recognized


def _schema_local_types(raw_types: object, *, schema_context: bool) -> list[str]:
    values: list[object]
    if isinstance(raw_types, (list, tuple, set)):
        values = list(raw_types)
    else:
        values = [raw_types]

    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        raw = value.strip()
        if not raw:
            continue
        local = _schema_uri_local_name(raw)
        if local is None and schema_context and _is_simple_type_token(raw):
            local = raw
        if local and local not in result:
            result.append(local)
    return result


def _schema_uri_local_name(value: str) -> str | None:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold().strip(".")
    if host not in {"schema.org", "www.schema.org"}:
        return None
    path = parsed.path.rstrip("/")
    local = path.rsplit("/", 1)[-1] if path else ""
    if not local and parsed.fragment:
        local = parsed.fragment
    return local or None


def _is_schema_context(value: str) -> bool:
    raw = str(value).strip()
    if not raw:
        return False
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").casefold().strip(".")
    return host in {"schema.org", "www.schema.org"}


def _is_simple_type_token(value: str) -> bool:
    return (
        ":" not in value
        and "/" not in value
        and "#" not in value
        and value.replace("_", "").replace("-", "").isalnum()
    )
