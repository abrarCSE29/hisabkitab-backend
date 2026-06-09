from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict:
    """Public health check — also the keep-alive ping target for Cron-Job.org."""
    return {"status": "healthy"}
