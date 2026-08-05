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
        # create_all 不会给已存在的表补列：strategy_runs.forward（信号前瞻收益）
        from sqlalchemy import inspect

        cols = {c["name"] for c in inspect(engine).get_columns("strategy_runs")}
        if "forward" not in cols:
            conn.execute(text("ALTER TABLE strategy_runs ADD COLUMN forward JSON"))
        user_cols = {c["name"] for c in inspect(engine).get_columns("users")}
        if "is_admin" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))
        conn.commit()

    from app.jobs.collect import (
        collect_once,
        refresh_reference,
        run_bootstrap_and_first_collect,
        snapshot_factors,
    )
    from app.jobs.strategy_runs import check_data_freshness, run_all_strategies

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
        # 盘后 15:25 CST（07:25 UTC）自动跑全部策略并推送命中变化；
        # 启动时补跑一次（内部时间闸门保证只在"当日收盘后"真正执行，幂等）。
        _scheduler.add_job(
            run_all_strategies,
            "cron",
            hour=7,
            minute=25,
            id="strategy_runs",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc),
        )
        # 数据断供告警：每 30 分钟自检一次（仅盘中生效，见函数内闸门）
        _scheduler.add_job(
            check_data_freshness,
            "interval",
            minutes=30,
            id="data_freshness",
            max_instances=1,
            coalesce=True,
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
