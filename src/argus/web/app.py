from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Path, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response

from argus.contracts.models import CollectionRequest
from argus.web.client import ArgusApiClient
from argus.web.config import WebSettings
from argus.web.security import basic_auth_dependency
from argus.web.ui_assets import APP_JS, INDEX_HTML, STYLE_CSS

_BROWSER_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self'; style-src 'self'; connect-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
_JSON_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}
_TERMINAL_ID_PATTERN = r"^[A-Za-z0-9._:-]+$"


def create_web_app(
    settings: WebSettings | None = None,
    *,
    api_client: ArgusApiClient | None = None,
) -> FastAPI:
    settings = settings or WebSettings()
    settings.ensure_dirs()
    client = api_client or ArgusApiClient(settings)
    require_user = basic_auth_dependency(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        await client.start()
        try:
            yield
        finally:
            await client.close()

    app = FastAPI(
        title="ARGUS Web UI",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    async def proxy(
        method: str,
        path: str,
        *,
        json_body: object | None = None,
        params: dict[str, object] | None = None,
    ) -> JSONResponse:
        try:
            status_code, payload = await client.request_json(
                method,
                path,
                json_body=json_body,
                params=params,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            return JSONResponse(
                status_code=502,
                content={
                    "detail": "local ARGUS API is unavailable",
                    "error_type": type(exc).__name__,
                },
                headers=_JSON_HEADERS,
            )
        return JSONResponse(status_code=status_code, content=payload, headers=_JSON_HEADERS)

    @app.get("/", dependencies=[Depends(require_user)], response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML, headers=_BROWSER_HEADERS)

    @app.get("/assets/style.css", dependencies=[Depends(require_user)])
    async def style() -> Response:
        return Response(STYLE_CSS, media_type="text/css", headers=_BROWSER_HEADERS)

    @app.get("/assets/app.js", dependencies=[Depends(require_user)])
    async def javascript() -> Response:
        return Response(APP_JS, media_type="application/javascript", headers=_BROWSER_HEADERS)

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "argus-web-ui",
            "api_url": settings.api_url,
        }

    @app.get("/api/health", dependencies=[Depends(require_user)])
    async def api_health() -> JSONResponse:
        return await proxy("GET", "/v1/health")

    @app.get("/api/capabilities", dependencies=[Depends(require_user)])
    async def capabilities() -> JSONResponse:
        return await proxy("GET", "/v1/capabilities")

    @app.get("/api/sources", dependencies=[Depends(require_user)])
    async def sources() -> JSONResponse:
        return await proxy("GET", "/v1/sources")

    @app.post("/api/collections", dependencies=[Depends(require_user)])
    async def create_collection(request: CollectionRequest) -> JSONResponse:
        return await proxy(
            "POST",
            "/v1/collections",
            json_body=request.model_dump(mode="json"),
        )

    @app.get("/api/collections/{collection_id}", dependencies=[Depends(require_user)])
    async def collection_status(
        collection_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=_TERMINAL_ID_PATTERN,
        ),
    ) -> JSONResponse:
        return await proxy("GET", f"/v1/collections/{collection_id}")

    @app.post(
        "/api/collections/{collection_id}/cancel",
        dependencies=[Depends(require_user)],
    )
    async def cancel_collection(
        collection_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=_TERMINAL_ID_PATTERN,
        ),
    ) -> JSONResponse:
        return await proxy("POST", f"/v1/collections/{collection_id}/cancel")

    @app.get(
        "/api/collections/{collection_id}/result/summary",
        dependencies=[Depends(require_user)],
    )
    async def result_summary(
        collection_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=_TERMINAL_ID_PATTERN,
        ),
    ) -> JSONResponse:
        return await proxy("GET", f"/v1/collections/{collection_id}/result/summary")

    @app.get(
        "/api/collections/{collection_id}/result/observations",
        dependencies=[Depends(require_user)],
    )
    async def observations(
        collection_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=_TERMINAL_ID_PATTERN,
        ),
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=2048),
    ) -> JSONResponse:
        params: dict[str, object] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return await proxy(
            "GET",
            f"/v1/collections/{collection_id}/result/observations",
            params=params,
        )

    @app.get(
        "/api/collections/{collection_id}/result/evidence",
        dependencies=[Depends(require_user)],
    )
    async def evidence(
        collection_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=_TERMINAL_ID_PATTERN,
        ),
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, max_length=2048),
    ) -> JSONResponse:
        params: dict[str, object] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        return await proxy(
            "GET",
            f"/v1/collections/{collection_id}/result/evidence",
            params=params,
        )

    return app
