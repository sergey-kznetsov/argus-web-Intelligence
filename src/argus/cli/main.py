from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
import typer
import uvicorn

from argus.api.app import create_app
from argus.cli.probe import render_probe_summary, run_embedded_probe
from argus.config import Settings, get_settings
from argus.contracts.models import (
    CollectionConstraints,
    CollectionRequest,
    Point,
    TerritoryContext,
)
from argus.security.auth import ensure_token, write_new_token

app = typer.Typer(no_args_is_help=True)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ensure_token(get_settings())}"}


def _base_url() -> str:
    settings = get_settings()
    return f"http://{settings.host}:{settings.port}"


@app.command("serve")
def serve() -> None:
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


@app.command("init-token")
def init_token(force: bool = typer.Option(False, "--force")) -> None:
    settings = get_settings()
    if settings.token_file.exists() and not force:
        typer.echo(f"Token already exists at {settings.token_file}")
        raise typer.Exit()
    write_new_token(settings.token_file)
    # Never print bearer secrets into shell history, CI logs or copied terminal output.
    typer.echo(f"Token written to {settings.token_file}")


@app.command("collect")
def collect(
    consumer: Annotated[str, typer.Option("--consumer")],
    address: Annotated[str, typer.Option("--address")],
    intent: Annotated[list[str], typer.Option("--intent")],
    analysis_id: Annotated[str, typer.Option("--analysis-id")] = "cli",
    seed_url: Annotated[list[str] | None, typer.Option("--seed-url")] = None,
) -> None:
    body = {
        "consumer": consumer,
        "analysis_id": analysis_id,
        "territory": {"address": address},
        "intents": intent,
        "constraints": {"seed_urls": seed_url or []},
        "allow_partial": True,
    }
    response = httpx.post(
        f"{_base_url()}/v1/collections",
        headers=_headers(),
        json=body,
        timeout=30,
    )
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command("probe")
def probe(
    intent: Annotated[list[str], typer.Option("--intent", help="Research intent; repeatable")],
    address: Annotated[str | None, typer.Option("--address")] = None,
    city: Annotated[str | None, typer.Option("--city")] = None,
    latitude: Annotated[float | None, typer.Option("--latitude")] = None,
    longitude: Annotated[float | None, typer.Option("--longitude")] = None,
    radius_meters: Annotated[int | None, typer.Option("--radius-meters")] = None,
    seed_url: Annotated[list[str] | None, typer.Option("--seed-url")] = None,
    allowed_domain: Annotated[list[str] | None, typer.Option("--allowed-domain")] = None,
    denied_domain: Annotated[list[str] | None, typer.Option("--denied-domain")] = None,
    language: Annotated[str | None, typer.Option("--language")] = None,
    max_pages: Annotated[int, typer.Option("--max-pages", min=1, max=500)] = 30,
    max_depth: Annotated[int, typer.Option("--max-depth", min=0, max=5)] = 2,
    timeout_seconds: Annotated[float, typer.Option("--timeout-seconds", min=1)] = 300.0,
    consumer: Annotated[str, typer.Option("--consumer")] = "standalone-probe",
    analysis_id: Annotated[str | None, typer.Option("--analysis-id")] = None,
    db_path: Annotated[Path, typer.Option("--db-path")] = Path(".argus/probe.sqlite3"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
    discovery: Annotated[
        bool,
        typer.Option("--discovery/--no-discovery", help="Enable configured web discovery"),
    ] = True,
    preview_items: Annotated[int, typer.Option("--preview-items", min=0, max=100)] = 10,
    preview_chars: Annotated[int, typer.Option("--preview-chars", min=0, max=10000)] = 500,
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Print the full JSON report to stdout as well"),
    ] = False,
) -> None:
    """Run a real ARGUS collection locally, without Geo Analyzer or a server worker."""

    if not intent:
        raise typer.BadParameter("at least one --intent is required")
    if (latitude is None) != (longitude is None):
        raise typer.BadParameter("--latitude and --longitude must be supplied together")
    if not any((address, city, latitude is not None)):
        raise typer.BadParameter("provide --address, --city, or coordinates")

    point = (
        Point(latitude=latitude, longitude=longitude)
        if latitude is not None and longitude is not None
        else None
    )
    territory = TerritoryContext(
        city=city,
        address=address,
        point=point,
        radius_meters=radius_meters,
    )
    request = CollectionRequest(
        consumer=consumer,
        analysis_id=analysis_id or f"probe-{uuid4().hex[:12]}",
        territory=territory,
        intents=intent,
        constraints=CollectionConstraints(
            max_pages=max_pages,
            max_depth=max_depth,
            allowed_domains=allowed_domain or [],
            denied_domains=denied_domain or [],
            seed_urls=seed_url or [],
            language=language,
        ),
        allow_partial=True,
    )

    settings = Settings(
        execution_role="embedded",
        storage_backend="sqlite",
        db_path=db_path,
    )
    if not discovery:
        settings = settings.model_copy(
            update={
                "browser_serp_enabled": False,
                "searxng_url": None,
            }
        )

    try:
        report = asyncio.run(
            run_embedded_probe(
                settings,
                request,
                timeout_seconds=timeout_seconds,
            )
        )
    except Exception as exc:
        typer.echo(f"ARGUS probe failed: {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    collection_id = str(report.get("result", {}).get("collection_id") or request.analysis_id)
    report_path = output or Path(".argus/probes") / f"{collection_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_json = json.dumps(report, ensure_ascii=False, indent=2)
    report_path.write_text(report_json + "\n", encoding="utf-8")

    typer.echo(
        render_probe_summary(
            report,
            preview_items=preview_items,
            preview_chars=preview_chars,
        )
    )
    typer.echo("")
    typer.echo(f"Full JSON report: {report_path.resolve()}")
    if json_stdout:
        typer.echo("")
        typer.echo(report_json)


@app.command("status")
def status_command(collection_id: str) -> None:
    response = httpx.get(
        f"{_base_url()}/v1/collections/{collection_id}",
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command("result")
def result_command(collection_id: str) -> None:
    response = httpx.get(
        f"{_base_url()}/v1/collections/{collection_id}/result",
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command("sources")
def sources_command() -> None:
    response = httpx.get(f"{_base_url()}/v1/sources", headers=_headers(), timeout=30)
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))
