from fastapi import APIRouter, Depends, Query

from app.core.categories import CATEGORIES, Category, CategoryType
from app.core.security import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[Category])
def list_categories(
    type: CategoryType | None = Query(default=None),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[Category]:
    """Bilingual (English/Bangla) category list, optionally filtered by type."""
    if type is None:
        return CATEGORIES
    return [category for category in CATEGORIES if category.type == type]
