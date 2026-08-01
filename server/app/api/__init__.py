from fastapi import APIRouter

from app.api import auth, backtests, health, quotes, screener, settings, strategies, watchlist

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(settings.router)
api_router.include_router(quotes.router)
api_router.include_router(screener.router)
api_router.include_router(strategies.router)
api_router.include_router(watchlist.router)
api_router.include_router(backtests.router)
