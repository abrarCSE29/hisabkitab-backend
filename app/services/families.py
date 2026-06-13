import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, status
from pymongo.database import Database

from app.core.security import AuthenticatedUser
from app.schemas.family import FamilyCreate, InviteRequest, JoinRequest
from app.services import email as email_service
from app.services import users as users_service
from app.services.vouchers import parse_family_id


def create_family(db: Database, user: AuthenticatedUser, payload: FamilyCreate) -> dict:
    users_service.upsert_user(db, user)
    document = {
        "name": payload.name,
        "created_by": user.id,
        "members": [
            {"user_id": user.id, "role": "admin", "email": user.email, "name": user.name}
        ],
        "invites": [],
        # A reusable, non-email-bound code the admin can share directly (chat,
        # in person). Regenerable so a leaked code can be revoked.
        "share_code": secrets.token_hex(4),
        "created_at": datetime.now(timezone.utc),
    }
    result = db.families.insert_one(document)
    return {"family_id": str(result.inserted_id), "name": payload.name}


def get_or_create_share_code(db: Database, user: AuthenticatedUser, family_id: str) -> str:
    """Return the family's shareable join code (admin only).

    Lazily mints one for families created before this feature existed.
    """
    family = _resolve_admin_family(db, user, family_id)
    code = family.get("share_code")
    if not code:
        code = secrets.token_hex(4)
        db.families.update_one({"_id": family["_id"]}, {"$set": {"share_code": code}})
    return code


def rotate_share_code(db: Database, user: AuthenticatedUser, family_id: str) -> str:
    """Revoke the current shareable code and issue a fresh one (admin only)."""
    family = _resolve_admin_family(db, user, family_id)
    code = secrets.token_hex(4)
    db.families.update_one({"_id": family["_id"]}, {"$set": {"share_code": code}})
    return code


def get_user_families(db: Database, user: AuthenticatedUser) -> list[dict]:
    families = list(db.families.find({"members.user_id": user.id}))

    # Enrich members with up-to-date profile data (name/email/avatar) from the
    # `users` collection so display stays fresh even when a member updated their
    # Google profile after joining. The name/email stored on the membership are
    # kept as a fallback for users who have not signed in since the sync existed.
    member_ids = {m["user_id"] for doc in families for m in doc["members"]}
    profiles = users_service.get_users_by_ids(db, member_ids)

    result = []
    for doc in families:
        doc["_id"] = str(doc["_id"])
        doc.pop("invites", None)  # join codes are not exposed to members
        for member in doc["members"]:
            profile = profiles.get(member["user_id"])
            member["name"] = (profile or {}).get("name") or member.get("name")
            member["email"] = (profile or {}).get("email") or member.get("email")
            member["avatar_url"] = (profile or {}).get("avatar_url")
        result.append(doc)
    return result


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


def _add_member(db: Database, family: dict, user: AuthenticatedUser) -> dict:
    if any(m["user_id"] == user.id for m in family["members"]):
        raise HTTPException(status.HTTP_409_CONFLICT, "You are already a member of this family")
    users_service.upsert_user(db, user)
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
            }
        },
    )
    return {"family_id": str(family["_id"]), "name": family["name"]}


def join_family(db: Database, user: AuthenticatedUser, payload: JoinRequest) -> dict:
    code = payload.code.strip()

    # A directly-shared family code lets anyone holding it join — no email binding.
    shared = db.families.find_one({"share_code": code})
    if shared is not None:
        return _add_member(db, shared, user)

    # Otherwise fall back to an emailed, single-use invite bound to the address.
    family = db.families.find_one({"invites.code": code})
    if family is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid join code")

    invite = next(i for i in family["invites"] if i["code"] == code)
    if invite["status"] != "pending":
        raise HTTPException(status.HTTP_410_GONE, "This invite has already been used")
    if user.email is None or user.email.lower() != invite["email"]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "This invite was issued to a different email"
        )
    if any(m["user_id"] == user.id for m in family["members"]):
        raise HTTPException(status.HTTP_409_CONFLICT, "You are already a member of this family")

    users_service.upsert_user(db, user)
    updated_invites = [
        {**i, "status": "accepted"} if i["code"] == code else i for i in family["invites"]
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
