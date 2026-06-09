from pymongo.database import Database

from app.db.mongodb import get_database


def get_db() -> Database:
    """Database dependency — overridable in tests with a mock database."""
    return get_database()
