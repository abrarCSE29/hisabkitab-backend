from fastapi import APIRouter, Depends, Query, status
from pymongo.database import Database

from app.api.deps import get_db
from app.core.security import AuthenticatedUser, get_current_user
from app.schemas.ocr import OcrRequest, ReceiptExtraction
from app.schemas.voucher import VoucherCreate, VoucherCreated, VoucherOut, VoucherUpdate
from app.services import ocr as ocr_service
from app.services import vouchers as voucher_service

router = APIRouter(prefix="/vouchers", tags=["vouchers"])


@router.post("", response_model=VoucherCreated, status_code=status.HTTP_201_CREATED)
def create_voucher(
    payload: VoucherCreate,
    db: Database = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> VoucherCreated:
    voucher_id = voucher_service.create_voucher(db, user, payload)
    return VoucherCreated(id=voucher_id)


@router.get("", response_model=list[VoucherOut], response_model_by_alias=True)
def get_vouchers(
    family_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[dict]:
    return voucher_service.list_vouchers(db, user, family_id=family_id, limit=limit)


@router.get("/{voucher_id}", response_model=VoucherOut, response_model_by_alias=True)
def get_voucher(
    voucher_id: str,
    db: Database = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Fetch one voucher — the caller's own, or from a family they belong to."""
    return voucher_service.get_voucher(db, user, voucher_id)


@router.put("/{voucher_id}", response_model=VoucherOut, response_model_by_alias=True)
def update_voucher(
    voucher_id: str,
    payload: VoucherUpdate,
    db: Database = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Replace a voucher's editable fields (creator only); total is recomputed."""
    return voucher_service.update_voucher(db, user, voucher_id, payload)


@router.post("/ocr", response_model=ReceiptExtraction)
def parse_receipt(
    payload: OcrRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ReceiptExtraction:
    """Extract itemized rows from a receipt image via the OpenAI Vision API."""
    return ocr_service.extract_receipt_items(payload.image_url)
