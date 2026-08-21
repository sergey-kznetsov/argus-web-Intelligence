from __future__ import annotations

import json
from typing import Annotated

import httpx
import typer
import uvicorn

from argus.api.app import create_app
from argus.config import get_settings
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
    token = write_new_token(settings.token_file)
    typer.echo(f"Token written to {settings.token_file}")
    typer.echo(token)


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
    response = httpx.post(f"{_base_url()}/v1/collections", headers=_headers(), json=body, timeout=30)
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command("status")
def status_command(collection_id: str) -> None:
    response = httpx.get(f"{_base_url()}/v1/collections/{collection_id}", headers=_headers(), timeout=30)
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command("result")
def result_command(collection_id: str) -> None:
    response = httpx.get(f"{_base_url()}/v1/collections/{collection_id}/result", headers=_headers(), timeout=30)
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command("sources")
def sources_command() -> None:
    response = httpx.get(f"{_base_url()}/v1/sources", headers=_headers(), timeout=30)
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))
