"""PyMongo connection lifecycle.

A single MongoClient is opened at application startup and closed at shutdown
so the connection pool is shared across requests — important on MongoDB
Atlas M0 where the connection cap is 500. Routes are declared as sync `def`,
so FastAPI runs them in its thread pool and blocking PyMongo calls never
stall the event loop.
"""

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from app.core.config import get_settings

_client: MongoClient | None = None


def connect_to_mongo() -> None:
    """Open the shared client and ensure indexes. Idempotent.

    Called from the ASGI lifespan locally, but also lazily from get_database()
    so the app still works on serverless platforms (e.g. Vercel) where the
    lifespan startup event is not driven by the runtime.
    """
    global _client
    if _client is not None:
        return
    settings = get_settings()
    _client = MongoClient(
        settings.mongodb_uri,
        maxPoolSize=10,  # stay well below the Atlas M0 connection cap
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=10000,
        retryWrites=True,
    )
    ensure_indexes(get_database())


def close_mongo_connection() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def get_database() -> Database:
    if _client is None:
        # Serverless cold start: the lifespan never ran, so connect on demand.
        connect_to_mongo()
    return _client[get_settings().mongodb_db_name]


def ensure_indexes(db: Database) -> None:
    """Index strategy from the architecture blueprint (idempotent)."""
    db.vouchers.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    db.vouchers.create_index([("family_id", ASCENDING), ("created_at", DESCENDING)])
    db.families.create_index([("members.user_id", ASCENDING)])
