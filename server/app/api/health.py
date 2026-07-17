from fastapi import APIRouter

from app.providers.factory import get_provider

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": get_provider().name}
