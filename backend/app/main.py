from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.schemas import MatchCatalog
from app.config import Settings
from app.store.local import load_catalog


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

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

    return app


app = create_app()
