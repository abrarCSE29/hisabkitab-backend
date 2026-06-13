import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import DEV_PLACEHOLDER_JWT_SECRET, get_settings
from app.core.logging import setup_logging
from app.db.mongodb import close_mongo_connection, connect_to_mongo

request_logger = logging.getLogger("hisabkitab.request")


@asynccontextmanager
async def lifespan(app: FastAPI):
    connect_to_mongo()
    yield
    close_mongo_connection()


def create_app() -> FastAPI:
    setup_logging()
    settings = get_settings()

    # Fail closed: with the public placeholder secret, anyone could mint valid
    # HS256 tokens. Refuse to boot outside explicit local development.
    if settings.supabase_jwt_secret == DEV_PLACEHOLDER_JWT_SECRET and not settings.debug:
        raise RuntimeError(
            "SUPABASE_JWT_SECRET is not configured (still the development placeholder). "
            "Set a real secret, or set DEBUG=true for local development."
        )

    # Expose the interactive API docs only in local development; keep the
    # schema and Swagger/ReDoc UIs off in production.
    docs_enabled = settings.debug
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Stack trace for crashes even where uvicorn isn't in front
            # (tests, alternative servers); uvicorn.error logs it too.
            request_logger.exception(
                "%s %s -> unhandled exception", request.method, request.url.path
            )
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        # Populated by get_current_user once the token is verified.
        user_id = getattr(request.state, "user_id", None)
        request_logger.info(
            "%s %s -> %d (%.1f ms) user=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            user_id or "-",
        )
        return response

    # CORS_ORIGINS=https://app.example.com,... in production; "*" for local dev.
    # Credentials are never combined with a wildcard origin.
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    allow_all = origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=not allow_all,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/")
    async def health_check():
        return {"status": "ok"}

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
