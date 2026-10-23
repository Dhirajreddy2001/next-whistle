from contextlib import asynccontextmanager
import json
import time
from collections.abc import Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from app.api.schemas import LiveMatchState, MatchCatalog
from app.config import Settings
from app.store.local import load_catalog


def create_app(settings: Settings | None = None, now_ms: Callable[[], int] | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.catalog = load_catalog(settings.catalog_path)
        yield
        app.state.catalog = None

    app = FastAPI(
        title="Next Whistle", version="0.1.0", lifespan=lifespan,
        openapi_url="/api/openapi.json", docs_url="/api/docs", redoc_url=None,
    )
    app.state.catalog = None
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.allowed_origins),
        allow_methods=["GET"], expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = f"req_{uuid4().hex}"
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    def problem(request: Request, status: int, title: str) -> JSONResponse:
        return JSONResponse(
            status_code=status, media_type="application/problem+json",
            content={"type": "about:blank", "title": title, "status": status,
                     "instance": request.url.path,
                     "requestId": request.state.request_id},
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return problem(request, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return problem(request, 400, "Invalid request")

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        if app.state.catalog is None:
            raise HTTPException(503, "Catalog not loaded")
        return {"status": "ready"}

    @app.get("/api/matches", response_model=MatchCatalog)
    async def matches():
        if app.state.catalog is None:
            raise HTTPException(503, "Catalog not loaded")
        return JSONResponse(
            content=app.state.catalog.model_dump(mode="json", by_alias=True),
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.get("/api/live-state", response_model=LiveMatchState)
    async def live_state(response: Response):
        response.headers["Cache-Control"] = "no-store"
        current_ms = now_ms()
        elapsed_ms = max(0, current_ms - settings.live_instance_epoch_ms)
        cycle_number, match_ms = divmod(elapsed_ms, settings.live_match_duration_ms)
        instance_start_ms = settings.live_instance_epoch_ms + cycle_number * settings.live_match_duration_ms
        return LiveMatchState(
            instance_id=f"li_{settings.live_match_id[2:]}_{cycle_number}",
            match_id=settings.live_match_id,
            server_time_ms=current_ms,
            instance_start_ms=instance_start_ms,
            match_ms=match_ms,
            duration_ms=settings.live_match_duration_ms,
            cycle_number=cycle_number,
        )

    def tracking_manifest(match_id: str) -> tuple[dict, Path]:
        known_match_ids = {item.match_id for item in app.state.catalog.matches} if app.state.catalog else set()
        if match_id not in known_match_ids:
            raise HTTPException(404, "Match not found")
        path = settings.dataset_path / match_id / "tracking" / "manifest.json"
        if not path.is_file():
            raise HTTPException(404, "Tracking not found")
        return json.loads(path.read_text()), path

    @app.get("/api/matches/{match_id}/tracking/manifest")
    async def get_tracking_manifest(match_id: str):
        _, path = tracking_manifest(match_id)
        return FileResponse(path, media_type="application/json", headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/api/matches/{match_id}/tracking/chunks/{chunk_index}")
    async def get_tracking_chunk(match_id: str, chunk_index: int):
        manifest, manifest_path = tracking_manifest(match_id)
        chunks = manifest.get("chunks", [])
        if chunk_index < 0 or chunk_index >= len(chunks):
            raise HTTPException(404, "Tracking chunk not found")
        path = manifest_path.parent.parent / chunks[chunk_index]["file"]
        if not path.is_file():
            raise HTTPException(404, "Tracking chunk not found")
        return FileResponse(path, media_type="application/json", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    return app


app = create_app()
