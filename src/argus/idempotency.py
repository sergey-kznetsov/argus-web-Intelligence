from __future__ import annotations

import hashlib
import json

from argus.contracts.models import CollectionRequest


def request_fingerprint(request: CollectionRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def storage_idempotency_key(request: CollectionRequest, fingerprint: str) -> str:
    explicit = (request.idempotency_key or "").strip()
    if explicit:
        namespace = f"explicit\x00{request.consumer}\x00{explicit}"
    else:
        namespace = f"auto\x00{fingerprint}"
    digest = hashlib.sha256(namespace.encode("utf-8")).hexdigest()
    return f"argus-v1:{digest}"
