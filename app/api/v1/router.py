from fastapi import APIRouter

from app.api.v1.endpoints import auth, categories, family, health, vouchers

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(vouchers.router)
api_router.include_router(categories.router)
api_router.include_router(family.router)
