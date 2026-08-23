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


def test_result_page_default_cannot_exceed_page_maximum():
    with pytest.raises(ValidationError):
        Settings(
            api_result_page_default_size=101,
            api_result_page_max_size=100,
        )


def test_result_delivery_defaults_are_bounded():
    settings = Settings()
    assert settings.api_full_result_max_items == 100
    assert settings.api_full_result_max_bytes == 4 * 1024 * 1024
    assert settings.api_result_page_default_size == 50
    assert settings.api_result_page_max_size == 100
    assert settings.api_result_page_max_bytes == 2 * 1024 * 1024


def test_pdf_extraction_defaults_are_bounded():
    settings = Settings()
    assert settings.pdf_max_bytes == 5 * 1024 * 1024
    assert settings.pdf_max_pages == 60
    assert settings.pdf_max_text_chars == 250_000
    assert settings.pdf_extract_timeout_seconds == 20
    assert settings.pdf_extract_memory_mb == 512


def test_transport_limit_can_be_stricter_than_pdf_parser_limit():
    settings = Settings(
        max_response_bytes=1024 * 1024,
        pdf_max_bytes=2 * 1024 * 1024,
    )
    assert settings.max_response_bytes == 1024 * 1024
    assert settings.pdf_max_bytes == 2 * 1024 * 1024


def test_idempotency_defaults_to_24_hours():
    assert Settings().idempotency_window_seconds == 86_400


def test_stale_worker_registration_retention_defaults_to_seven_days():
    assert Settings().retention_worker_registration_days == 7


def test_server_roles_require_postgresql():
    with pytest.raises(ValidationError):
        Settings(execution_role="api", storage_backend="sqlite")
    with pytest.raises(ValidationError):
        Settings(execution_role="worker", storage_backend="sqlite")
