from fastapi import APIRouter, Depends

from app.core.security import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=AuthenticatedUser)
def read_current_user(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    """Return the identity encoded in the caller's Supabase access token.

    Lets the frontend confirm a session is still valid server-side.
    """
    return user
