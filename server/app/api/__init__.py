from fastapi import APIRouter

from app.api import (
    admin,
    admin_users,
    auth,
    backtests,
    health,
    paper,
    quotes,
    research,
    screener,
    sector,
    settings,
    strategies,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(admin_users.router)
api_router.include_router(settings.router)
api_router.include_router(quotes.router)
api_router.include_router(screener.router)
api_router.include_router(sector.router)
api_router.include_router(strategies.router)
api_router.include_router(backtests.router)
api_router.include_router(paper.router)
api_router.include_router(research.router)
api_router.include_router(research.admin_router)
