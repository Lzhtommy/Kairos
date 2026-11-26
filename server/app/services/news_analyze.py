"""要闻二次分析：细分受影响方向 + 文中点名的 A 股 + 一句传导逻辑。

只做 4 分以上的根事件（每天一两百条），每批 BATCH 条单独调一次 LLM，不改动 news_classify 的批量标注。
放在标注之后、推送之前跑，推送消息能带上传导逻辑。

2026-11-26 离线核对了 11-20~25 的 655 条要闻（3 个有效交易日，样本小）：
- 模型给的行业方向：对开盘跳空命中 82%（按日×行业去重后 63%），对全天只有 35%（去重后 47%），
  和前一天行业走势一致的比例是 72%——主要在复述已发生的行情。所以方向只存进 impact 留档，不展示。
- 点名个股全部来自正文、没有编造，但有名称对不上（华虹公司 / 库里叫华虹宏力）和港股混入（联想），
  所以按 stock_info 在市股票精确对名称，对不上就丢。
- 细分方向（油运、出口链、家电出口）和传导逻辑是现有一级行业标注没有的信息。

整批调用失败不写，下一轮重试；模型漏掉的事件写空结果、记 analyzed_at，不再重试。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import StockInfo
from app.models.news import NewsEvent
from app.models.sector import SectorInfo
from app.services import llm
from app.services.news import _build_event, _load

logger = logging.getLogger("kairos.news")

MIN_SCORE = 4
BATCH = 8
MAX_BATCHES = 4
LOOKBACK = timedelta(hours=24)
TEXT_CHARS = 400
MAX_THEMES = 3
MAX_STOCKS = 5
REASON_CHARS = 60
CST = timezone(timedelta(hours=8))

_PROMPT = """你是 A 股研究员。逐条判断下面这些要闻对 A 股的影响。

字段：
- a：对 A 股有没有可交易的影响（当天盘中，或收盘后 / 非交易时段发布的看下一个交易日）。1 有，0 没有或很弱。
  只是海外市场自身的行情、和 A 股没有清晰传导链的地缘表态、汇总回顾类，填 0。
- m：对 A 股大盘的方向，1 偏正面，-1 偏负面，0 不明确或无关。
- sec：受影响的申万一级行业及方向，[{{"n": "行业", "d": 1 或 -1}}]，最多 3 个，只填传导逻辑明确的，没有就给空列表。
  行业只能从这份名单里选：{sectors}
- sub：更细的受影响方向（申万二级行业或市场常用的概念，如「半导体设备」「黄金」「航运」），最多 3 个字符串，没有给空列表。
- stk：文中明确点名、在 A 股上市的公司，[{{"n": "公司简称", "d": 1 或 -1}}]。不要联想受益股；只在港股或美股上市的公司不填。
- r：一句话说明传导逻辑，不超过 40 字。a 为 0 时说明为什么影响有限。

注意：消息发布时 A 股可能已经反映过，比如盘后发布的海外行情，你要判断的是它对接下来 A 股的影响方向。
不确定方向时宁可填 0，不要硬判。

输出严格 JSON：{{"items": [{{"i": 编号, "a": 0或1, "m": 1/0/-1, "sec": [...], "sub": [...], "stk": [...], "r": "..."}}]}}，每条都要有。

要闻：
{lines}"""


def _pending(db: Session, now: datetime) -> list[int]:
    return list(db.scalars(
        select(NewsEvent.event_id)
        .where(NewsEvent.parent_id.is_(None), NewsEvent.score >= MIN_SCORE, NewsEvent.analyzed_at.is_(None),
               NewsEvent.created_at >= now - LOOKBACK)
        .order_by(NewsEvent.event_id.desc())
        .limit(BATCH * MAX_BATCHES)
    ))


def _text(e: dict) -> str:
    t = f"{e['title']}。{e['content']}" if e["title"] else e["content"]
    return t[:TEXT_CHARS]


def _dir(v) -> int:  # noqa: ANN001
    try:
        return max(-1, min(1, int(v)))
    except (TypeError, ValueError):
        return 0


def _named(xs, key: str = "n") -> list[tuple[str, int]]:  # noqa: ANN001
    """[{"n": 名称, "d": 方向}] → [(名称, 方向)]；模型偶尔直接给字符串，当作方向不明。"""
    out = []
    for x in xs if isinstance(xs, list) else []:
        if isinstance(x, dict) and isinstance(x.get(key), str):
            out.append((x[key].strip(), _dir(x.get("d"))))
        elif isinstance(x, str):
            out.append((x.strip(), 0))
    return [(n, d) for n, d in out if n]


def analyze_batch(events: list[dict], sector_names: list[str], stocks: dict[str, str]) -> dict[int, dict]:
    """一批事件 → {event_id: {themes, ai_stocks, reason, impact}}。调用失败原样抛出。

    stocks 是在市股票「名称 → 代码」，点名个股按它精确对名称，对不上的丢掉。
    """
    lines = []
    for i, e in enumerate(events):
        t = datetime.fromisoformat(e["pub_at"].replace("Z", "+00:00")).astimezone(CST)
        lines.append(f"{i}. [{t:%m-%d %a %H:%M} 北京时间] {_text(e)}")
    prompt = _PROMPT.format(sectors="、".join(sector_names), lines="\n".join(lines))
    payload, tokens = llm.chat_json([{"role": "user", "content": prompt}], max_tokens=4096, timeout=90)
    logger.info("news analyze: %d events, %d tokens", len(events), tokens)

    names = set(sector_names)
    out: dict[int, dict] = {}
    for x in payload.get("items") or []:
        try:
            i = int(x.get("i"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not 0 <= i < len(events):
            continue
        themes = [s.strip() for s in (x.get("sub") or []) if isinstance(s, str) and s.strip()][:MAX_THEMES]
        picked: dict[str, str] = {}
        for n, _ in _named(x.get("stk")):
            code = stocks.get(n.replace(" ", ""))
            if code:
                picked.setdefault(code, n.replace(" ", ""))
        reason = x.get("r") if isinstance(x.get("r"), str) else ""
        out[events[i]["id"]] = {
            "themes": themes,
            "ai_stocks": [{"code": c, "name": n} for c, n in picked.items()][:MAX_STOCKS],
            "reason": reason.strip()[:REASON_CHARS] or None,
            "impact": {
                "a": 1 if _dir(x.get("a")) > 0 else 0,
                "m": _dir(x.get("m")),
                "sec": [{"n": n, "d": d} for n, d in _named(x.get("sec")) if n in names][:3],
                "stk": [{"n": n, "d": d} for n, d in _named(x.get("stk"))],
            },
        }
    return out


def analyze_pending(db: Session, now: datetime | None = None) -> int:
    """分析一轮，返回写入的事件数。"""
    if not llm.enabled():
        return 0
    now = now or datetime.now(timezone.utc)
    ids = _pending(db, now)
    if not ids:
        return 0
    groups, anns = _load(db, ids)
    events = [_build_event(groups[i]) for i in ids if i in groups]
    sector_names = list(db.scalars(select(SectorInfo.name).order_by(SectorInfo.code)))
    stocks = {n.replace(" ", ""): c for c, n in db.execute(
        select(StockInfo.code, StockInfo.name).where(StockInfo.listed()))}
    written = 0
    for k in range(0, len(events), BATCH):
        batch = sorted(events[k:k + BATCH], key=lambda e: e["id"])
        try:
            result = analyze_batch(batch, sector_names, stocks)
        except Exception as exc:  # noqa: BLE001 — 整批失败不写，下一轮重试
            logger.warning("news analyze failed: %s", exc)
            break
        for e in batch:
            r = result.get(e["id"]) or {}
            row = anns[e["id"]]
            row.themes = r.get("themes") or []
            row.ai_stocks = r.get("ai_stocks") or []
            row.reason = r.get("reason")
            row.impact = r.get("impact")
            row.analyzed_at = now
        db.commit()
        written += len(batch)
    return written
