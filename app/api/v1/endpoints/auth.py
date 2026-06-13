import logging

from fastapi import APIRouter, Depends
from pymongo.database import Database
from pymongo.errors import PyMongoError

from app.api.deps import get_db
from app.core.security import AuthenticatedUser, get_current_user
from app.services import users as users_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=AuthenticatedUser)
def read_current_user(
    user: AuthenticatedUser = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> AuthenticatedUser:
    """Return the caller's identity and record it in the `users` collection.

    The frontend calls this right after a Supabase sign-in, so it doubles as
    the profile-sync hook. The upsert is best-effort: a database hiccup must
    not stop a client from confirming its session is still valid.
    """
    try:
        users_service.upsert_user(db, user)
    except PyMongoError as exc:
        logger.warning("Failed to upsert user %s on sign-in: %s", user.id, exc)
    return user
