from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from argus.config import Settings


async def build_request_manager(settings: Settings, alias: str) -> tuple[Any, Any]:
    """Build an in-memory Crawlee queue, optionally wrapped by native domain throttling."""
    try:
        from crawlee.request_loaders import ThrottlingRequestManager
        from crawlee.storage_clients import MemoryStorageClient
        from crawlee.storages import RequestQueue
    except ImportError as exc:
        raise RuntimeError("Crawlee is required for crawler runtime") from exc

    storage_client = MemoryStorageClient()
    queue = await RequestQueue.open(alias=alias, storage_client=storage_client)
    domains = sorted({item.lower().strip(".") for item in settings.throttled_domains if item})
    if not domains:
        return storage_client, queue

    manager = ThrottlingRequestManager(
        inner=queue,
        domains=domains,
        request_manager_opener=RequestQueue.open,
        base_delay=timedelta(seconds=settings.throttle_base_delay_seconds),
        max_delay=timedelta(seconds=settings.throttle_max_delay_seconds),
    )
    if settings.per_domain_delay_seconds > 0:
        delay = max(1, math.ceil(settings.per_domain_delay_seconds))
        for domain in domains:
            manager.set_crawl_delay(f"https://{domain}/", delay)
    return storage_client, manager
