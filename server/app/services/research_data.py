"""研报原始数据采集：akshare 各源 → records（list[dict]）→ research_raw_cache。

- 每个源独立 try，失败只记录、不中断流水线（缺失由评分层按「数据不足」处理）。
- 每次调用套线程超时：akshare 部分接口内部重试会挂好几分钟（财联社前车之鉴）。
- 抓取失败但有旧缓存时回退旧缓存，并在 sources 里标 stale。
- 东财系接口（zygc / news / research_report）按「可能不可用」设计，缺了不影响评分主干。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.research import ResearchRawCache

logger = logging.getLogger("kairos.research")

CALL_TIMEOUT = 40  # 秒
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="research-fetch")


def sina_symbol(code: str) -> str:
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("4", "8", "9")):
        return f"bj{code}"
    return f"sz{code}"


def _ak():
    import akshare as ak  # noqa: PLC0415 — 可选依赖（requirements-data.txt）

    return ak


def _records(df: Any) -> list[dict]:
    # 经 pandas 自带 JSON 序列化：NaN → null、时间戳 → ISO 字符串，结果可直接进 JSON 列
    return json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8) - timedelta(days=days)).strftime("%Y%m%d")


def _today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y%m%d")


# source key → (label, 抓取函数, 缓存 TTL 小时)
SOURCES: dict[str, tuple[str, Callable[[str], Any], int]] = {
    "profile": ("巨潮资讯·公司概况", lambda c: _ak().stock_profile_cninfo(symbol=c), 24 * 30),
    "income": (
        "新浪财经·利润表",
        lambda c: _ak().stock_financial_report_sina(stock=sina_symbol(c), symbol="利润表"),
        24 * 7,
    ),
    "balance": (
        "新浪财经·资产负债表",
        lambda c: _ak().stock_financial_report_sina(stock=sina_symbol(c), symbol="资产负债表"),
        24 * 7,
    ),
    "cashflow": (
        "新浪财经·现金流量表",
        lambda c: _ak().stock_financial_report_sina(stock=sina_symbol(c), symbol="现金流量表"),
        24 * 7,
    ),
    "abstract_annual": (
        "同花顺·财务摘要（年度）",
        lambda c: _ak().stock_financial_abstract_ths(symbol=c, indicator="按年度"),
        24 * 7,
    ),
    "abstract_report": (
        "同花顺·财务摘要（报告期）",
        lambda c: _ak().stock_financial_abstract_ths(symbol=c, indicator="按报告期"),
        24 * 7,
    ),
    "dividend": ("巨潮资讯·分红", lambda c: _ak().stock_dividend_cninfo(symbol=c), 24 * 7),
    "zygc": ("东方财富·主营构成", lambda c: _ak().stock_zygc_em(symbol=sina_symbol(c).upper()), 24 * 7),
    "notices": (
        "巨潮资讯·公告",
        lambda c: _ak().stock_zh_a_disclosure_report_cninfo(
            symbol=c, market="沪深京", start_date=_since(180), end_date=_today()
        ),
        24,
    ),
    "news": ("东方财富·个股新闻", lambda c: _ak().stock_news_em(symbol=c), 6),
    "reports": ("东方财富·机构研报", lambda c: _ak().stock_research_report_em(symbol=c), 24),
}


def fetch_all(db: Session, code: str, progress: Callable[[str], None] | None = None) -> tuple[dict, list]:
    """抓全部源。返回 (raw: {source: records|None}, sources: [{key,label,fetched_at,status,error?}])。"""
    raw: dict[str, list[dict] | None] = {}
    sources: list[dict] = []
    now = datetime.now(timezone.utc)
    for key, (label, fn, ttl_h) in SOURCES.items():
        if progress:
            progress(f"采集：{label}")
        cached = db.get(ResearchRawCache, (key, code))
        cached_at = _aware(cached.fetched_at) if cached else None
        if cached and cached_at and now - cached_at < timedelta(hours=ttl_h):
            raw[key] = cached.payload
            sources.append({"key": key, "label": label, "fetched_at": cached_at.isoformat(), "status": "cache"})
            continue
        try:
            df = _pool.submit(fn, code).result(timeout=CALL_TIMEOUT)
            records = _records(df)
        except FutureTimeout:
            records, err = None, f"超时（>{CALL_TIMEOUT}s）"
        except Exception as exc:  # noqa: BLE001 — 单源失败不中断
            records, err = None, f"{type(exc).__name__}: {str(exc)[:120]}"
        if records is not None:
            if cached:
                cached.payload, cached.fetched_at = records, now
            else:
                db.add(ResearchRawCache(source=key, code=code, payload=records, fetched_at=now))
            db.commit()
            raw[key] = records
            sources.append({"key": key, "label": label, "fetched_at": now.isoformat(), "status": "ok"})
        elif cached:
            logger.info("Research source %s/%s failed (%s), using stale cache", key, code, err)
            raw[key] = cached.payload
            sources.append(
                {"key": key, "label": label, "fetched_at": cached_at.isoformat() if cached_at else None,
                 "status": "stale", "error": err}
            )
        else:
            logger.info("Research source %s/%s failed: %s", key, code, err)
            raw[key] = None
            sources.append({"key": key, "label": label, "fetched_at": None, "status": "failed", "error": err})
    return raw, sources


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
