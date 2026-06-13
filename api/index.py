"""Vercel serverless entrypoint.

Vercel's Python runtime detects the `app` ASGI application exported here and
serves it. All routes are forwarded to this function via the rewrite in
vercel.json, so FastAPI handles its own routing ("/" and "/api/v1/...").
"""

from app.main import app

__all__ = ["app"]
