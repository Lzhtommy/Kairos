from fastapi import APIRouter

from app.api import (
    admin,
    auth,
    backtests,
    health,
    paper,
    quotes,
    screener,
    sector,
    settings,
    strategies,
    watchlist,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(settings.router)
api_router.include_router(quotes.router)
api_router.include_router(screener.router)
api_router.include_router(sector.router)
api_router.include_router(strategies.router)
api_router.include_router(watchlist.router)
api_router.include_router(backtests.router)
api_router.include_router(paper.router)
