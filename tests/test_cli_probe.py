from __future__ import annotations

import contextlib
import http.server
import socketserver
import threading
from pathlib import Path

import pytest
from click.utils import strip_ansi
from typer.testing import CliRunner

from argus.cli.main import app
from argus.cli.probe import render_probe_summary, run_embedded_probe
from argus.config import Settings
from argus.contracts.models import (
    CollectionConstraints,
    CollectionRequest,
    CollectionStatus,
    TerritoryContext,
)


class _ProbeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"""<!doctype html>
<html>
  <head>
    <title>ARGUS Standalone Probe</title>
    <meta name="description" content="Evidence-first standalone test page">
  </head>
  <body>
    <main>
      <h1>Public test fact</h1>
      <p>Local deterministic probe. The standalone collector reached the factual page.</p>
    </main>
  </body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextlib.contextmanager
def _server():
    with socketserver.TCPServer(("127.0.0.1", 0), _ProbeHandler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            thread.join()


def test_probe_command_is_exposed_by_console_app() -> None:
    result = CliRunner().invoke(app, ["probe", "--help"])
    assert result.exit_code == 0, result.output
    help_text = strip_ansi(result.output)
    assert "--address" in help_text
    assert "--seed-url" in help_text
    assert "--no-discovery" in help_text
    assert "--output" in help_text
    assert "--require-covered-intents" in help_text


@pytest.mark.asyncio
async def test_embedded_probe_collects_observation_and_evidence_without_geo_analyzer(
    tmp_path: Path,
) -> None:
    with _server() as port:
        url = f"http://127.0.0.1:{port}/fact"
        settings = Settings(
            execution_role="embedded",
            storage_backend="sqlite",
            db_path=tmp_path / "probe.sqlite3",
            allow_internal_targets=["127.0.0.1"],
            browser_serp_enabled=False,
            sitemap_discovery_enabled=False,
            agent_enabled=False,
            max_concurrency=1,
            browser_max_concurrency=1,
        )
        request = CollectionRequest(
            consumer="standalone-probe-test",
            analysis_id="probe-integration",
            territory=TerritoryContext(address="Local deterministic probe"),
            intents=["public_mentions"],
            constraints=CollectionConstraints(
                max_pages=1,
                max_depth=0,
                seed_urls=[url],
            ),
        )

        report = await run_embedded_probe(settings, request, timeout_seconds=30)

    result = report["result"]
    assert result["status"] == CollectionStatus.COMPLETED.value
    assert len(result["observations"]) >= 1
    assert len(result["evidence"]) >= 1
    assert any(item["url"] == url for item in result["observations"])
    assert any(item["source"]["url"] == url for item in result["evidence"])
    assert any(
        "standalone collector reached the factual page" in (item.get("text") or "").lower()
        for item in result["observations"]
    )
    assert report["probe"]["mode"] == "embedded"
    assert report["probe"]["storage_backend"] == "sqlite"
    assert "generic_web" in report["source_health"]

    acceptance = report["acceptance"]
    assert acceptance["fully_covered"] is True
    assert acceptance["covered_intents"] == ["public_mentions"]
    assert acceptance["uncovered_intents"] == []
    assert acceptance["intent_source_counts"]["public_mentions"] >= 1
    assert acceptance["model_output_is_evidence"] is False

    summary = render_probe_summary(report, preview_items=2, preview_chars=120)
    assert "Status: completed" in summary
    assert "Intent coverage: 1/1" in summary
    assert "public_mentions: covered" in summary
    assert "Observation preview:" in summary
    assert "Evidence preview:" in summary


def test_probe_strict_coverage_exits_two_after_saving_report(monkeypatch, tmp_path: Path) -> None:
    from argus.cli import main as cli_main

    async def fake_probe(*args, **kwargs):
        del args, kwargs
        return {
            "probe": {"elapsed_seconds": 0.1},
            "acceptance": {
                "requested_intents": ["reviews", "complaints"],
                "covered_intents": ["reviews"],
                "uncovered_intents": ["complaints"],
                "intent_source_counts": {"reviews": 1, "complaints": 0},
                "covered_count": 1,
                "requested_count": 2,
                "fully_covered": False,
                "semantic_excerpt_evidence_count": 0,
                "public_map_providers_with_facts": ["2gis_web"],
                "model_output_is_evidence": False,
            },
            "collection": {"stage": "completed"},
            "result": {
                "collection_id": "strict-probe",
                "status": "partial",
                "observations": [],
                "evidence": [],
                "coverage": [],
                "errors": [],
            },
            "source_health": {},
            "metrics": {},
        }

    monkeypatch.setattr(cli_main, "run_embedded_probe", fake_probe)
    report_path = tmp_path / "strict.json"
    result = CliRunner().invoke(
        app,
        [
            "probe",
            "--address",
            "Ижевск",
            "--intent",
            "reviews",
            "--intent",
            "complaints",
            "--output",
            str(report_path),
            "--require-covered-intents",
        ],
    )

    assert result.exit_code == 2
    assert report_path.exists()
    assert "Intent coverage: 1/2" in result.output
    assert "complaints: uncovered" in result.output
    assert "ARGUS probe acceptance failed; uncovered intents: complaints" in result.output
