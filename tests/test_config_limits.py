import pytest
from pydantic import ValidationError

from argus.config import Settings


def test_consumer_queue_limit_cannot_exceed_global_limit():
    with pytest.raises(ValidationError):
        Settings(
            queue_max_active_collections=10,
            queue_max_active_per_consumer=11,
        )


def test_worker_heartbeat_must_be_shorter_than_lease():
    with pytest.raises(ValidationError):
        Settings(
            worker_lease_seconds=30,
            worker_heartbeat_seconds=30,
        )


def test_snapshot_retention_cannot_be_shorter_than_collection_retention():
    with pytest.raises(ValidationError):
        Settings(
            retention_collection_days=365,
            retention_snapshot_days=180,
        )


def test_idempotency_defaults_to_24_hours():
    assert Settings().idempotency_window_seconds == 86_400


def test_server_roles_require_postgresql():
    with pytest.raises(ValidationError):
        Settings(execution_role="api", storage_backend="sqlite")
    with pytest.raises(ValidationError):
        Settings(execution_role="worker", storage_backend="sqlite")
