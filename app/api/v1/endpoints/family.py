from fastapi import APIRouter, Depends, status
from pymongo.database import Database

from app.api.deps import get_db
from app.core.ratelimit import SlidingWindowLimiter, user_rate_limit
from app.core.security import AuthenticatedUser, get_current_user
from app.schemas.family import (
    FamilyCreate,
    FamilyCreated,
    FamilyOut,
    InviteRequest,
    InviteResponse,
    JoinRequest,
)
from app.services import families as family_service

router = APIRouter(prefix="/family", tags=["family"])

# Join codes are short; cap guessing attempts per user.
JOIN_LIMITER = SlidingWindowLimiter(max_requests=10, window_seconds=900)


@router.post("", response_model=FamilyCreated, status_code=status.HTTP_201_CREATED)
def create_family(
    payload: FamilyCreate,
    db: Database = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Establish a family entity with the caller as its admin."""
    return family_service.create_family(db, user, payload)


@router.get("", response_model=list[FamilyOut], response_model_by_alias=True)
def list_my_families(
    db: Database = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[dict]:
    return family_service.get_user_families(db, user)


@router.post("/invite", response_model=InviteResponse)
def invite_member(
    payload: InviteRequest,
    db: Database = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> InviteResponse:
    """Generate a join code for the invitee and send it by email."""
    family_service.invite_member(db, user, payload)
    return InviteResponse()


@router.post(
    "/join",
    response_model=FamilyCreated,
    dependencies=[Depends(user_rate_limit(JOIN_LIMITER, "join"))],
)
def join_family(
    payload: JoinRequest,
    db: Database = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Redeem an emailed join code to become a family member."""
    return family_service.join_family(db, user, payload)
