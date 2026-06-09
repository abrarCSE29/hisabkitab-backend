import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
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
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # tighten to the Vercel domain in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
