import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, status
from pymongo.database import Database

from app.core.security import AuthenticatedUser
from app.schemas.family import FamilyCreate, InviteRequest, JoinRequest
from app.services import email as email_service
from app.services.vouchers import parse_family_id


def create_family(db: Database, user: AuthenticatedUser, payload: FamilyCreate) -> dict:
    document = {
        "name": payload.name,
        "created_by": user.id,
        "members": [
            {"user_id": user.id, "role": "admin", "email": user.email, "name": user.name}
        ],
        "invites": [],
        "created_at": datetime.now(timezone.utc),
    }
    result = db.families.insert_one(document)
    return {"family_id": str(result.inserted_id), "name": payload.name}


def get_user_families(db: Database, user: AuthenticatedUser) -> list[dict]:
    families = []
    for doc in db.families.find({"members.user_id": user.id}):
        doc["_id"] = str(doc["_id"])
        doc.pop("invites", None)  # join codes are not exposed to members
        families.append(doc)
    return families


def _resolve_admin_family(db: Database, user: AuthenticatedUser, family_id: str | None) -> dict:
    """Find the family the invite applies to, requiring the caller be its admin."""
    if family_id is not None:
        family = db.families.find_one({"_id": parse_family_id(family_id)})
        if family is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Family not found")
        role = next(
            (m["role"] for m in family["members"] if m["user_id"] == user.id), None
        )
        if role != "admin":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Only a family admin can send invites"
            )
        return family

    admin_families = list(
        db.families.find({"members": {"$elemMatch": {"user_id": user.id, "role": "admin"}}})
    )
    if not admin_families:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "You do not administer any family — create one first"
        )
    if len(admin_families) > 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "You administer multiple families — specify family_id",
        )
    return admin_families[0]


def invite_member(db: Database, user: AuthenticatedUser, payload: InviteRequest) -> None:
    family = _resolve_admin_family(db, user, payload.family_id)

    email_lower = payload.email.lower()
    if any((m.get("email") or "").lower() == email_lower for m in family["members"]):
        raise HTTPException(status.HTTP_409_CONFLICT, "User is already a member")

    # Re-inviting replaces any pending code for that email instead of stacking.
    db.families.update_one(
        {"_id": family["_id"]},
        {"$pull": {"invites": {"email": email_lower, "status": "pending"}}},
    )

    join_code = secrets.token_hex(4)  # 8-char shareable code
    db.families.update_one(
        {"_id": family["_id"]},
        {
            "$push": {
                "invites": {
                    "email": payload.email.lower(),
                    "code": join_code,
                    "invited_by": user.id,
                    "status": "pending",
                    "created_at": datetime.now(timezone.utc),
                }
            }
        },
    )
    email_service.send_invite_email(payload.email, family["name"], join_code)


def join_family(db: Database, user: AuthenticatedUser, payload: JoinRequest) -> dict:
    family = db.families.find_one({"invites.code": payload.code})
    if family is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid join code")

    invite = next(i for i in family["invites"] if i["code"] == payload.code)
    if invite["status"] != "pending":
        raise HTTPException(status.HTTP_410_GONE, "This invite has already been used")
    if user.email is None or user.email.lower() != invite["email"]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This invite was issued to a different email"
        )
    if any(m["user_id"] == user.id for m in family["members"]):
        raise HTTPException(status.HTTP_409_CONFLICT, "You are already a member of this family")

    updated_invites = [
        {**i, "status": "accepted"} if i["code"] == payload.code else i
        for i in family["invites"]
    ]
    db.families.update_one(
        {"_id": family["_id"]},
        {
            "$push": {
                "members": {
                    "user_id": user.id,
                    "role": "member",
                    "email": user.email,
                    "name": user.name,
                }
            },
            "$set": {"invites": updated_invites},
        },
    )
    return {"family_id": str(family["_id"]), "name": family["name"]}
