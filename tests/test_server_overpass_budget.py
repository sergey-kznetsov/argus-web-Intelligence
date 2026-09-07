from argus.bootstrap import (
    SERVER_DEFAULT_OVERPASS_URL,
    SERVER_OVERPASS_MAX_RETRIES,
    SERVER_OVERPASS_TIMEOUT_SECONDS,
    effective_map_settings,
)
from argus.config import Settings


def test_auto_enabled_server_overpass_is_bounded_inside_source_task_budget():
    settings = Settings(
        execution_role="worker",
        overpass_url=None,
        overpass_timeout_seconds=30.0,
        direct_provider_max_retries=2,
    )

    effective = effective_map_settings(settings)

    assert effective.overpass_url == SERVER_DEFAULT_OVERPASS_URL
    assert effective.overpass_timeout_seconds == SERVER_OVERPASS_TIMEOUT_SECONDS
    assert effective.direct_provider_max_retries == SERVER_OVERPASS_MAX_RETRIES
    assert settings.overpass_url is None
    assert settings.overpass_timeout_seconds == 30.0
    assert settings.direct_provider_max_retries == 2


def test_explicit_server_overpass_configuration_remains_operator_owned():
    settings = Settings(
        execution_role="worker",
        overpass_url="https://overpass.example/api/interpreter",
        overpass_timeout_seconds=28.0,
        direct_provider_max_retries=2,
    )

    assert effective_map_settings(settings) is settings


def test_embedded_runtime_does_not_auto_enable_overpass():
    settings = Settings(
        execution_role="embedded",
        overpass_url=None,
        overpass_timeout_seconds=30.0,
        direct_provider_max_retries=2,
    )

    assert effective_map_settings(settings) is settings
