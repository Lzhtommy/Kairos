"""快讯事件 LLM 标注：分类 + 1-5 重要度 + 相关申万一级行业。

每分钟一轮：取近 24 小时还没标注的事件（只看事件首条），新的优先，每批 30 个，一轮最多 4 批。
整批调用失败就什么都不写，下一轮重试；模型漏掉的事件写一行空标注，不再重试
（前端对空标注按源标记规则兜底）。

重要度看的是「对 A 股或全球主要市场的影响」，不照搬各家编辑的标记——各家尺度差很多
（华尔街见闻约四成快讯都标了重要），直接用会让「要闻」密度失控。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models.news import NewsEvent, NewsItem
from app.models.sector import SectorInfo
from app.services import llm
from app.services.news import _build_event, _load

logger = logging.getLogger("kairos.news")

CATEGORIES = ["宏观数据", "央行货币", "国内政策", "A股盘面", "公司个股", "科技产业", "海外市场", "商品外汇", "地缘国际",
              "其他"]
BATCH = 30
MAX_BATCHES = 4
LOOKBACK = timedelta(hours=24)
TEXT_CHARS = 160
MAX_SECTORS = 3

_RUBRIC = """分类（必须从下列中选一个）：
- 宏观数据：经济数据发布与预告（GDP、CPI、PMI、就业、贸易、社融等，国内外都算）
- 央行货币：央行利率与流动性操作、货币政策表态（人民银行、美联储、欧央行、日央行等）
- 国内政策：国务院与部委政策、监管规则、产业扶持、地方政策
- A股盘面：A 股指数、板块、资金流向、涨跌停、ETF 成交等盘面信息
- 公司个股：单个公司的公告、业绩、并购、人事、增减持、评级（含港美股公司）
- 科技产业：AI、半导体、算力、机器人、新能源技术等产业与技术动态——主要内容是技术或产业趋势的
  归这里（如大厂改变芯片采购方式、AI 投资规模预测），即使发言人是央行官员或消息来自海外；
  单个公司的常规公告仍归「公司个股」
- 海外市场：港股、美股、欧日韩等海外股市和股指期货
- 商品外汇：原油、黄金、有色、农产品、汇率、国债收益率
- 地缘国际：外交、战争冲突、制裁、关税与贸易摩擦、选举等国际政治
- 其他：灾害、社会新闻、汇总与预告类、广告等

重要度（对 A 股或全球主要市场的影响）：
5 = 重大，可能影响全市场：降准降息、美联储议息结果、重大地缘冲突爆发、重磅监管或刺激政策、全市场级暴涨暴跌
4 = 重要，对某个市场或行业有明确影响：关键经济数据、行业级政策、龙头公司重大事件、主要指数大幅异动
3 = 一般市场信息：常规数据、普通盘面异动、中型公司的重要公告
2 = 影响小：小公司公告、常规会见表态、机构观点
1 = 几乎无市场意义：每日汇总、日程预告、社会新闻"""


def _pending(db: Session, now: datetime) -> list[int]:
    head = NewsItem.id == NewsItem.event_id
    done = exists().where(NewsEvent.event_id == NewsItem.event_id)
    return list(db.scalars(
        select(NewsItem.event_id)
        .where(head, NewsItem.seen_at >= now - LOOKBACK, ~done)
        .order_by(NewsItem.event_id.desc())
        .limit(BATCH * MAX_BATCHES)
    ))


def _clip_score(v) -> int | None:  # noqa: ANN001
    try:
        return max(1, min(5, int(v)))
    except (TypeError, ValueError):
        return None


def classify_batch(events: list[dict], sector_names: list[str]) -> dict[int, dict]:
    """一批事件 → {event_id: {category, score, sectors}}。调用失败原样抛出。"""
    lines = []
    for i, e in enumerate(events):
        text = f"{e['title']}。{e['content']}" if e["title"] else e["content"]
        lines.append(f"{i}. {text[:TEXT_CHARS]}")
    sector_part = (
        f"\n\n相关行业只能从这份申万一级行业名单里选，最多 {MAX_SECTORS} 个，没有明确相关的给空列表："
        f"{'、'.join(sector_names)}"
        if sector_names else ""
    )
    prompt = (
        "你是财经新闻编辑。给下面每条快讯标注分类、重要度和相关行业。\n\n"
        f"{_RUBRIC}{sector_part}\n\n"
        '输出严格 JSON：{"items": [{"i": 编号, "c": "分类", "s": 重要度, "sec": ["行业"]}]}，每条快讯都要有。\n\n'
        "快讯：\n" + "\n".join(lines)
    )
    payload, tokens = llm.chat_json([{"role": "user", "content": prompt}], max_tokens=4096, timeout=90)
    logger.info("news classify: %d events, %d tokens", len(events), tokens)

    names = set(sector_names)
    out: dict[int, dict] = {}
    for x in payload.get("items") or []:
        try:
            i = int(x.get("i"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not 0 <= i < len(events):
            continue
        cat = x.get("c")
        out[events[i]["id"]] = {
            "category": cat if cat in CATEGORIES else None,
            "score": _clip_score(x.get("s")),
            "sectors": [s for s in (x.get("sec") or []) if isinstance(s, str) and s in names][:MAX_SECTORS],
        }
    return out


def classify_pending(db: Session, now: datetime | None = None) -> int:
    """标注一轮，返回写入的事件数。"""
    if not llm.enabled():
        return 0
    now = now or datetime.now(timezone.utc)
    ids = _pending(db, now)
    if not ids:
        return 0
    groups, _ = _load(db, ids)
    events = [_build_event(groups[i]) for i in ids if i in groups]
    sector_names = list(db.scalars(select(SectorInfo.name).order_by(SectorInfo.code)))
    model = llm.model_name() or ""
    written = 0
    for k in range(0, len(events), BATCH):
        batch = events[k:k + BATCH]
        try:
            result = classify_batch(batch, sector_names)
        except Exception as exc:  # noqa: BLE001 — 整批失败不写，下一轮重试
            logger.warning("news classify failed: %s", exc)
            break
        for e in batch:
            r = result.get(e["id"], {"category": None, "score": None, "sectors": []})
            db.add(NewsEvent(event_id=e["id"], model=model, **r))
        db.commit()
        written += len(batch)
    return written
