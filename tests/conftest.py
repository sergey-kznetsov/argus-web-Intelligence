from __future__ import annotations

import os

import psycopg
import pytest


_POSTGRES_TEST_NODE_MARKERS = (
    "postgres",
    "worker_database_lease_failure",
)


@pytest.fixture(autouse=True)
def disable_required_ollama_in_test_environment(monkeypatch: pytest.MonkeyPatch):
    """Run ordinary automated tests without requiring a local Ollama daemon.

    Production Settings and the standalone Windows deployment require Ollama by default.
    Tests that exercise the required-LLM path pass ``llm_required=True`` explicitly, so this
    fixture only keeps unrelated unit/integration tests independent of runner-local services.
    """

    monkeypatch.setenv("ARGUS_LLM_REQUIRED", "false")
    yield


@pytest.fixture(autouse=True)
def isolate_postgres_schema(request: pytest.FixtureRequest):
    """Give every PostgreSQL integration test an independent ARGUS schema.

    The CI service intentionally uses one PostgreSQL database for the whole pytest run.
    Queue/lease tests must therefore not inherit active collections, worker registrations,
    recipes or migration probes from an earlier test. Dropping only the ARGUS schema keeps
    the PostgreSQL service itself stable while making test ordering irrelevant.
    """

    dsn = os.getenv("ARGUS_TEST_POSTGRES_DSN", "").strip()
    node_id = request.node.nodeid.casefold()
    if not dsn or not any(marker in node_id for marker in _POSTGRES_TEST_NODE_MARKERS):
        yield
        return

    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS argus CASCADE")
    yield
