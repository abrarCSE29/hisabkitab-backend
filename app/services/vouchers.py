from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException, status
from pymongo.database import Database

from app.core.security import AuthenticatedUser
from app.schemas.voucher import VoucherCreate, VoucherItem

TWO_PLACES = Decimal("0.01")


def compute_voucher_total(items: list[VoucherItem]) -> float:
    """Sum item amounts in Decimal space to avoid float drift (0.1 + 0.2 == 0.3)."""
    total = sum(Decimal(str(item.amount)) for item in items)
    return float(total.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def parse_family_id(raw: str) -> ObjectId:
    try:
        return ObjectId(raw)
    except (InvalidId, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="family_id is not a valid ObjectId",
        )


def assert_family_member(db: Database, family_id: ObjectId, user: AuthenticatedUser) -> None:
    membership = db.families.find_one({"_id": family_id, "members.user_id": user.id})
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this family",
        )


def create_voucher(db: Database, user: AuthenticatedUser, payload: VoucherCreate) -> str:
    family_oid = None
    if payload.family_id is not None:
        family_oid = parse_family_id(payload.family_id)
        assert_family_member(db, family_oid, user)

    document = {
        "family_id": family_oid,
        "user_id": user.id,
        "type": payload.type,
        "category_id": payload.category_id,
        "items": [item.model_dump() for item in payload.items],
        "voucher_total": compute_voucher_total(payload.items),
        "image_url": payload.image_url,
        "created_at": datetime.now(timezone.utc),
    }
    result = db.vouchers.insert_one(document)
    return str(result.inserted_id)


def list_vouchers(
    db: Database,
    user: AuthenticatedUser,
    family_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Reverse-chronological feed, scoped per the blueprint's visibility rules.

    Solo mode filters on the caller's user_id; an explicit family_id switches
    the scope to the shared family feed (membership required).
    """
    if family_id is not None:
        family_oid = parse_family_id(family_id)
        assert_family_member(db, family_oid, user)
        query = {"family_id": family_oid}
    else:
        query = {"user_id": user.id}

    cursor = db.vouchers.find(query).sort("created_at", -1).limit(limit)
    documents = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        if doc.get("family_id") is not None:
            doc["family_id"] = str(doc["family_id"])
        documents.append(doc)
    return documents
