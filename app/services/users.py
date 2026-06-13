"""User profile persistence.

Supabase owns authentication, but the app keeps its own lightweight `users`
collection so it can associate identities with families and show member names
and avatars even for people who are not the current viewer. A document is
upserted on every sign-in (via /auth/me), keyed by the Supabase user UUID.
"""

from collections.abc import Iterable
from datetime import datetime, timezone

from pymongo.database import Database

from app.core.security import AuthenticatedUser


def upsert_user(db: Database, user: AuthenticatedUser) -> dict | None:
    """Record/refresh the caller's profile. Called on sign-in.

    name/avatar are only written when present so a later token that happens to
    lack `user_metadata` (e.g. a refreshed session) never wipes a previously
    captured Google profile. Email/password accounts simply have no avatar.
    """
    now = datetime.now(timezone.utc)
    set_fields: dict = {"last_seen_at": now}
    if user.email is not None:
        set_fields["email"] = user.email
    if user.name is not None:
        set_fields["name"] = user.name
    if user.avatar_url is not None:
        set_fields["avatar_url"] = user.avatar_url

    db.users.update_one(
        {"_id": user.id},
        {"$set": set_fields, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return db.users.find_one({"_id": user.id})


def get_users_by_ids(db: Database, user_ids: Iterable[str]) -> dict[str, dict]:
    """Fetch profiles for the given Supabase UUIDs, keyed by id."""
    ids = list(set(user_ids))
    if not ids:
        return {}
    return {doc["_id"]: doc for doc in db.users.find({"_id": {"$in": ids}})}
