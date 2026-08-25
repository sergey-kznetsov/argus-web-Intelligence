from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from argus.contracts.models import CollectionRecord, Evidence, Observation, Snapshot
from argus.recipes.models import SiteRecipe


class IdempotencyConflictError(RuntimeError):
    """An explicit idempotency key was reused for a different request."""


class QueueCapacityError(RuntimeError):
    """A new collection cannot be admitted because an active queue limit is full."""

    def __init__(self, scope: str, current: int, limit: int) -> None:
        self.scope = scope
        self.current = current
        self.limit = limit
        super().__init__(f"{scope} active collection limit reached: {current}/{limit}")


@dataclass(frozen=True, slots=True)
class QueueMetrics:
    queued: int
    running: int
    active_leases: int
    expired_leases: int
    active_workers: int
    stale_workers: int
    oldest_queued_age_seconds: float | None
    oldest_running_age_seconds: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "queued": self.queued,
            "running": self.running,
            "active_leases": self.active_leases,
            "expired_leases": self.expired_leases,
            "active_workers": self.active_workers,
            "stale_workers": self.stale_workers,
            "oldest_queued_age_seconds": self.oldest_queued_age_seconds,
            "oldest_running_age_seconds": self.oldest_running_age_seconds,
        }


@dataclass(frozen=True, slots=True)
class RetentionResult:
    idempotency_deleted: int = 0
    collections_deleted: int = 0
    snapshots_deleted: int = 0
    workers_deleted: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "idempotency_deleted": self.idempotency_deleted,
            "collections_deleted": self.collections_deleted,
            "snapshots_deleted": self.snapshots_deleted,
            "workers_deleted": self.workers_deleted,
        }


class Repository(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def health(self) -> dict[str, object]: ...
    async def create_collection(self, record: CollectionRecord) -> None: ...
    async def create_collection_idempotent(
        self,
        record: CollectionRecord,
        *,
        idempotency_key: str,
        request_hash: str,
        idempotency_window_seconds: int | None = None,
        max_active_collections: int | None = None,
        max_active_per_consumer: int | None = None,
    ) -> tuple[CollectionRecord, bool]: ...
    async def get_collection(self, collection_id: str) -> CollectionRecord | None: ...
    async def list_recoverable_collections(self) -> list[CollectionRecord]: ...
    async def update_collection(self, record: CollectionRecord) -> None: ...
    async def add_observation(self, observation: Observation) -> None: ...
    async def list_observations(self, collection_id: str) -> list[Observation]: ...
    async def find_observation_by_content_hash(
        self,
        collection_id: str,
        *,
        content_hash: str,
        source_kinds: list[str],
    ) -> Observation | None: ...
    async def add_evidence(self, evidence: Evidence, collection_id: str) -> None: ...
    async def list_evidence(self, collection_id: str) -> list[Evidence]: ...
    async def add_snapshot(
        self,
        snapshot: Snapshot,
        collection_id: str | None = None,
    ) -> None: ...
    async def latest_snapshot(self, source_url: str) -> Snapshot | None: ...
    async def save_recipe(self, recipe: SiteRecipe) -> None: ...
    async def get_recipe(self, domain: str, goal: str) -> SiteRecipe | None: ...
    async def prune_recipe_versions(
        self,
        domain: str,
        goal: str,
        *,
        keep_versions: int,
    ) -> int: ...


class AtomicTaskRepository(Repository, Protocol):
    async def commit_task_success(
        self,
        record: CollectionRecord,
        *,
        observations: list[Observation],
        evidence: list[Evidence],
        snapshots: list[Snapshot],
    ) -> None: ...


class WorkerQueueRepository(Repository, Protocol):
    async def register_worker(
        self,
        worker_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    async def heartbeat_worker(self, worker_id: str) -> bool: ...
    async def unregister_worker(self, worker_id: str) -> None: ...
    async def active_worker_count(self, *, max_age_seconds: float) -> int: ...

    async def queue_metrics(self, *, worker_max_age_seconds: float) -> QueueMetrics: ...

    async def run_retention(
        self,
        *,
        idempotency_window_seconds: int,
        collection_retention_days: int,
        snapshot_retention_days: int,
        worker_registration_retention_days: int,
        result_access_grace_seconds: int = 3600,
        batch_size: int,
    ) -> RetentionResult: ...

    async def claim_next_collection(
        self,
        worker_id: str,
        *,
        lease_seconds: float,
    ) -> str | None: ...

    async def renew_collection_lease(
        self,
        collection_id: str,
        worker_id: str,
        *,
        lease_seconds: float,
    ) -> bool: ...

    async def release_collection_lease(
        self,
        collection_id: str,
        worker_id: str,
    ) -> None: ...
