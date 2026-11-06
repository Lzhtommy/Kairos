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
    from sqlalchemy import inspect, text

    with engine.connect() as conn:
        # MySQL 不支持 CREATE INDEX IF NOT EXISTS，先查再建
        kline_idx = {i["name"] for i in inspect(engine).get_indexes("kline")}
        if "ix_kline_code_period_ts" not in kline_idx:
            conn.execute(text("CREATE INDEX ix_kline_code_period_ts ON kline (code, period, ts)"))
        # create_all 不会给已存在的表补列：strategy_runs.forward（信号前瞻收益）
        cols = {c["name"] for c in inspect(engine).get_columns("strategy_runs")}
        if "forward" not in cols:
            conn.execute(text("ALTER TABLE strategy_runs ADD COLUMN forward JSON"))
        user_cols = {c["name"] for c in inspect(engine).get_columns("users")}
        if "is_admin" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))
        conn.commit()

    from app.jobs.backup import backup_database
    from app.jobs.collect import (
        collect_once,
        refresh_reference,
        run_bootstrap_and_first_collect,
        snapshot_factors,
    )
    from app.jobs.hot_rank import record_daily_hot_ranks, refresh_market_hot_ranks
    from app.jobs.narrative import update_narrative
    from app.jobs.research import process_research_queue, requeue_interrupted
    from app.jobs.security_master import refresh_security_master, security_master_empty
    from app.jobs.sector import update_sectors
    from app.jobs.strategy_runs import check_data_freshness, run_all_strategies

    run_bootstrap_and_first_collect()
    requeue_interrupted()

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
        # 收盘后 15:10 CST（07:10 UTC）强制补采一轮：盘中最后一采可能早于收盘集合
        # 竞价成交，当日 bar 存的不是正式收盘价，次日开盘会被除权检测误判、整段
        # 历史被错误缩放。赶在 15:15 因子快照之前。
        _scheduler.add_job(
            collect_once,
            "cron",
            day_of_week="mon-fri",
            hour=7,
            minute=10,
            kwargs={"force": True},
            id="close_collect",
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
        # 行业板块日更（K 线 + 拥挤度 + 月度倾斜）：盘后 15:40 CST（07:40 UTC）
        # + 次日盘前 08:40 CST（00:40 UTC）。申万当日 K 线常到深夜甚至次日才发布，
        # 只跑盘后那一轮会让页面一直落后一个交易日，盘前这轮负责补齐。
        # 启动时补跑一次（幂等 + 盘中自动跳过，首轮会全量 bootstrap 板块历史）。
        _scheduler.add_job(
            update_sectors,
            "cron",
            hour="0,7",
            minute=40,
            id="sector_update",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc),
        )
        # 盘后 15:50 CST（07:50 UTC）行业叙事打分（DeepSeek，只前向记录）；
        # 周末也跑——政策叙事常发生在非交易日。
        _scheduler.add_job(
            update_narrative,
            "cron",
            hour=7,
            minute=50,
            id="narrative_update",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc),
        )
        # 研报任务队列：每 20s 取一条 queued 串行执行（生成一份约数分钟）
        _scheduler.add_job(
            process_research_queue,
            "interval",
            seconds=20,
            id="research_queue",
            max_instances=1,
            coalesce=True,
        )
        # 自选股人气排名日更：00:30 CST（16:30 UTC），前一日排名此时已定稿；
        # 启动时补跑一次（已落库的跳过，幂等）。
        _scheduler.add_job(
            record_daily_hot_ranks,
            "cron",
            hour=16,
            minute=30,
            id="hot_rank_daily",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc),
        )
        # 盘中每 10 分钟刷新全市场实时人气快照（选股命中上千只时用），启动即跑一次
        _scheduler.add_job(
            refresh_market_hot_ranks,
            "interval",
            minutes=10,
            id="hot_rank_market",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc),
        )
        # 证券主表（含退市股）日更：17:30 CST（09:30 UTC），盘后任务都跑完之后；
        # 表为空（首次部署）时启动即跑一次。BaoStock 全量约 40 秒。
        _scheduler.add_job(
            refresh_security_master,
            "cron",
            day_of_week="mon-fri",
            hour=9,
            minute=30,
            id="security_master",
            max_instances=1,
            coalesce=True,
            **({"next_run_time": datetime.now(timezone.utc)} if security_master_empty() else {}),
        )
        # 凌晨 03:00 CST（19:00 UTC）SQLite 备份，避开盘中采集与盘后任务
        _scheduler.add_job(
            backup_database,
            "cron",
            hour=19,
            minute=0,
            id="db_backup",
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
