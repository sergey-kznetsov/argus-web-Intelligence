from __future__ import annotations

import hashlib
from uuid import UUID, uuid5

_ARGUS_IDENTITY_NAMESPACE = UUID("9d955f3d-e3fe-4b93-9082-51833ab0b2a7")


def _stable_id(kind: str, *parts: str | None) -> str:
    payload = "\x1f".join([kind, *(part or "" for part in parts)])
    return str(uuid5(_ARGUS_IDENTITY_NAMESPACE, payload))


def stable_observation_id(
    collection_id: str,
    source_id: str,
    entity_type: str,
    source_url: str,
    content_hash: str,
    entity_id: str | None = None,
) -> str:
    """Return an idempotent Observation ID within one collection.

    Reprocessing the same source entity with the same content after a process restart produces the same ID,
    while a changed document or a different collection remains a distinct observation.
    """

    return _stable_id(
        "observation",
        collection_id,
        source_id,
        entity_type,
        entity_id,
        source_url,
        content_hash,
    )


def stable_evidence_id(
    observation_id: str,
    evidence_type: str,
    source_url: str,
    text: str,
) -> str:
    """Return an idempotent Evidence ID tied to its observation and evidence content."""

    text_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return _stable_id(
        "evidence",
        observation_id,
        evidence_type,
        source_url,
        text_hash,
    )
