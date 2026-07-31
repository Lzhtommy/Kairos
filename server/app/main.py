import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.config import settings
from app.core.db import Base, engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # per-request lines drown the collector logs
logger = logging.getLogger("kairos")

_scheduler: BackgroundScheduler | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Create tables (Alembic manages migrations in prod; create_all is a safe no-op if current).
    Base.metadata.create_all(bind=engine)
    # K 线到 4M+ 行后，_sparks / 回测的按股取数没有复合索引会全表扫
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_kline_code_period_ts ON kline (code, period, ts)")
        )
        conn.commit()

    from app.jobs.collect import (
        collect_once,
        refresh_reference,
        run_bootstrap_and_first_collect,
        snapshot_factors,
    )

    run_bootstrap_and_first_collect()

    global _scheduler
    if settings.enable_collector:
        _scheduler = BackgroundScheduler(timezone="UTC")
        _scheduler.add_job(
            collect_once,
            "interval",
            seconds=settings.collect_interval_seconds,
            id="collect",
            max_instances=1,
            coalesce=True,
        )
        # Slow-moving reference data (industry / roe / dividend). next_run_time=now
        # runs it right after startup in a worker thread so boot isn't blocked.
        _scheduler.add_job(
            refresh_reference,
            "interval",
            hours=24,
            id="reference",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc),
        )
        # 收盘后 15:15 CST（07:15 UTC）归档因子快照；启动时补拍一次，
        # 覆盖服务器当天收盘后才启动的情况（幂等 + 盘中自动跳过）。
        _scheduler.add_job(
            snapshot_factors,
            "cron",
            hour=7,
            minute=15,
            id="factor_snapshot",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc),
        )
        _scheduler.start()
        logger.info("Collector scheduled every %ds", settings.collect_interval_seconds)

    yield

    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="Kairos API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.cors_origins == "*" else settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
def root() -> dict:
    return {"name": "Kairos API", "docs": "/docs", "health": "/api/health"}
