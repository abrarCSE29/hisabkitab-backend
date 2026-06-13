from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException, status
from pymongo.database import Database

from app.core.security import AuthenticatedUser
from app.schemas.voucher import VoucherCreate, VoucherItem, VoucherUpdate

TWO_PLACES = Decimal("0.01")


def compute_voucher_total(items: list[VoucherItem]) -> float:
    """Sum item amounts in Decimal space to avoid float drift (0.1 + 0.2 == 0.3)."""
    total = sum(Decimal(str(item.amount)) for item in items)
    return float(total.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def parse_object_id(raw: str, field: str) -> ObjectId:
    try:
        return ObjectId(raw)
    except (InvalidId, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field} is not a valid ObjectId",
        )


def parse_family_id(raw: str) -> ObjectId:
    return parse_object_id(raw, "family_id")


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
        # Display identity for shared family feeds (from Google profile when present)
        "user_email": user.email,
        "user_name": user.name,
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

    Solo mode shows only the caller's *personal* entries (those not tied to any
    family); an explicit family_id switches the scope to the shared family feed
    (membership required). Without the family_id filter, entries a user logs in
    a family would also leak into their personal feed.
    """
    if family_id is not None:
        family_oid = parse_family_id(family_id)
        assert_family_member(db, family_oid, user)
        query = {"family_id": family_oid}
    else:
        query = {"user_id": user.id, "family_id": None}

    cursor = db.vouchers.find(query).sort("created_at", -1).limit(limit)
    return [_serialize(doc) for doc in cursor]


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    if doc.get("family_id") is not None:
        doc["family_id"] = str(doc["family_id"])
    return doc


def _fetch_visible_voucher(db: Database, user: AuthenticatedUser, voucher_id: str) -> dict:
    """Load a voucher the caller may see: their own, or one in a family
    they belong to. Anything else is reported as not found (no existence leak)."""
    oid = parse_object_id(voucher_id, "voucher_id")
    doc = db.vouchers.find_one({"_id": oid})
    if doc is not None:
        if doc["user_id"] == user.id:
            return doc
        family_id = doc.get("family_id")
        if family_id is not None and db.families.find_one(
            {"_id": family_id, "members.user_id": user.id}
        ):
            return doc
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voucher not found")


def get_voucher(db: Database, user: AuthenticatedUser, voucher_id: str) -> dict:
    return _serialize(_fetch_visible_voucher(db, user, voucher_id))


def update_voucher(
    db: Database, user: AuthenticatedUser, voucher_id: str, payload: VoucherUpdate
) -> dict:
    doc = _fetch_visible_voucher(db, user, voucher_id)
    if doc["user_id"] != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator of a voucher can edit it",
        )

    changes = {
        "type": payload.type,
        "category_id": payload.category_id,
        "items": [item.model_dump() for item in payload.items],
        "voucher_total": compute_voucher_total(payload.items),
        "image_url": payload.image_url,
        "updated_at": datetime.now(timezone.utc),
    }
    db.vouchers.update_one({"_id": doc["_id"]}, {"$set": changes})
    return _serialize({**doc, **changes})


def delete_voucher(db: Database, user: AuthenticatedUser, voucher_id: str) -> None:
    doc = _fetch_visible_voucher(db, user, voucher_id)
    if doc["user_id"] != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator of a voucher can delete it",
        )
    db.vouchers.delete_one({"_id": doc["_id"]})
