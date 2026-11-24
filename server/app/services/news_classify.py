"""快讯事件 LLM 标注：分类 + 1-5 重要度 + 相关申万一级行业。

每分钟一轮：取近 24 小时还没标注的事件（只看事件首条），新的优先，每批 30 个，一轮最多 4 批。
整批调用失败就什么都不写，下一轮重试；模型漏掉的事件写一行空标注，不再重试
（前端对空标注按源标记规则兜底）。

重要度看的是「对 A 股或全球主要市场的影响」，不照搬各家编辑的标记——各家尺度差很多
（华尔街见闻约四成快讯都标了重要），直接用会让「要闻」密度失控。

同时让模型判断每条是不是更早某个事件的后续（p 字段）：2026-11-21 凌晨一小时 40 条要闻里，
美联储理事库克一段讲话占了 11 条、费城半导体指数「跌 5%→6%→7%→9%」各一条、伊朗爆炸
三家报道没合并成一个。候选父事件 = 本批之前 THREAD_WINDOW 内 3 分以上的根事件 + 本批里更早的条目。
后续事件写 parent_id，不单独显示；分数比父事件高时把父事件抬到同分（整串的重要度取最高）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.models.news import NewsEvent, NewsItem
from app.models.sector import SectorInfo
from app.services import llm
from app.services.news import _build_event, _iso, _load

logger = logging.getLogger("kairos.news")

CATEGORIES = ["宏观数据", "央行货币", "国内政策", "A股盘面", "公司个股", "科技产业", "海外市场", "商品外汇", "地缘国际",
              "其他"]
BATCH = 30
MAX_BATCHES = 4
LOOKBACK = timedelta(hours=24)
TEXT_CHARS = 160
MAX_SECTORS = 3
THREAD_WINDOW = timedelta(minutes=60)  # 父事件最早可以比本条早多久
MAX_ANCHORS = 40
ANCHOR_CHARS = 60

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
2 = 影响小：小公司公告、常规会见表态
1 = 几乎无市场意义：每日汇总、日程预告、社会新闻、没有实质内容的条目
另外：券商、机构、分析师的观点和预测最高 3 分；市井消费见闻（如抢购金条）最高 2 分。"""

_THREAD = """后续判断（p 字段）：如果这条快讯和「已有事件」里的某条、或本批编号更小的某条说的是同一件事，
p 填那一条（已有事件填 "A编号"，本批填数字编号），否则不填。算同一件事的情况：
- 同一人同一场讲话 / 采访的另一句表态
- 同一个指数、品种、个股同一波行情的进一步变化（如跌幅从 5% 扩大到 7%、金价接连突破整数关口）
- 同一个市场同一时段的大盘行情：开盘、盘中、收盘前后的指数刷新都算一串，几个主要指数（如美股标普、
  道指、纳指，A 股沪指、深成指、创业板指）放在一起报或分开报都算同一件事
- 其他媒体对同一事件的报道或进展（同一次爆炸、同一份公告、同一项数据）
不算的情况（宁可不挂）：
- 同一话题下的不同事件，如两项不同的经济数据（服务业 PMI 和综合 PMI 也算不同）、两个不同公司的公告、
  同一领导人谈不同议题
- 不同品种或不同市场的行情，即使方向相同、原因相同：黄金和白银、国际金价和 A 股黄金股、
  沪锡和 LME 锡、不同国家或地区的股市（美股和日股、日股和韩股）
- 某个事件引发的大盘、指数、债市行情：如公司重大事件后「美股三大股指高开」「费城半导体指数大涨」
  「美债收益率上升」，这些是独立的市场事件，不挂到公司事件下"""



def _pending(db: Session, now: datetime) -> list[int]:
    head = NewsItem.id == NewsItem.event_id
    done = exists().where(NewsEvent.event_id == NewsItem.event_id)
    return list(db.scalars(
        select(NewsItem.event_id)
        .where(head, NewsItem.seen_at >= now - LOOKBACK, ~done)
        .order_by(NewsItem.event_id.desc())
        .limit(BATCH * MAX_BATCHES)
    ))


def _anchors(db: Session, batch: list[dict]) -> list[tuple[int, str]]:
    """候选父事件：本批最早一条之前 THREAD_WINDOW 内、3 分以上的根事件，新的优先。"""
    lo = min(e["pub_at"] for e in batch)
    lo_dt = datetime.fromisoformat(lo.replace("Z", "+00:00"))
    first_id = min(e["id"] for e in batch)
    pub = func.min(NewsItem.pub_at)
    ids = list(db.scalars(
        select(NewsEvent.event_id)
        .join(NewsItem, NewsItem.event_id == NewsEvent.event_id)
        .where(NewsEvent.parent_id.is_(None), NewsEvent.score >= 3, NewsEvent.event_id < first_id)
        .group_by(NewsEvent.event_id)
        .having(pub >= lo_dt - THREAD_WINDOW)
        .order_by(NewsEvent.event_id.desc())
        .limit(MAX_ANCHORS)
    ))
    if not ids:
        return []
    groups, _ = _load(db, ids)
    out = []
    for i in sorted(ids):
        if i in groups:
            e = _build_event(groups[i])
            out.append((i, (e["title"] or e["content"])[:ANCHOR_CHARS]))
    return out


def _clip_score(v) -> int | None:  # noqa: ANN001
    try:
        return max(1, min(5, int(v)))
    except (TypeError, ValueError):
        return None


def classify_batch(events: list[dict], sector_names: list[str],
                   anchors: list[tuple[int, str]] | None = None) -> dict[int, dict]:
    """一批事件（按时间正序）→ {event_id: {category, score, sectors, parent}}。调用失败原样抛出。

    parent 是模型指认的「同一件事」：已有事件的 event_id，或本批更早一条的 event_id（未解析到根）。
    """
    anchors = anchors or []
    lines = []
    for i, e in enumerate(events):
        text = f"{e['title']}。{e['content']}" if e["title"] else e["content"]
        lines.append(f"{i}. {text[:TEXT_CHARS]}")
    sector_part = (
        f"\n\n相关行业只能从这份申万一级行业名单里选，最多 {MAX_SECTORS} 个，没有明确相关的给空列表："
        f"{'、'.join(sector_names)}"
        if sector_names else ""
    )
    anchor_part = (
        "已有事件（近一小时的重要快讯）：\n" + "\n".join(f"A{k}. {t}" for k, (_, t) in enumerate(anchors)) + "\n\n"
        if anchors else ""
    )
    prompt = (
        "你是财经新闻编辑。给下面每条快讯标注分类、重要度、相关行业，并判断是不是已有事件的后续。\n\n"
        f"{_RUBRIC}{sector_part}\n\n{_THREAD}\n\n"
        '输出严格 JSON：{"items": [{"i": 编号, "c": "分类", "s": 重要度, "sec": ["行业"], "p": "A3" 或 编号 或 null}]}，'
        "每条快讯都要有。\n\n"
        f"{anchor_part}快讯（按时间先后）：\n" + "\n".join(lines)
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
            "parent": _parent_ref(x.get("p"), i, events, anchors),
        }
    return out


def _parent_ref(p, i: int, events: list[dict], anchors: list[tuple[int, str]]) -> int | None:  # noqa: ANN001
    """模型给的 p → event_id。只接受已有事件或本批更早的条目，其他一律当作没有。"""
    if p is None or p == "":
        return None
    ps = str(p).strip()
    if ps[:1] in ("A", "a"):
        try:
            k = int(ps[1:])
        except ValueError:
            return None
        return anchors[k][0] if 0 <= k < len(anchors) else None
    try:
        j = int(ps)
    except ValueError:
        return None
    return events[j]["id"] if 0 <= j < i else None


def classify_pending(db: Session, now: datetime | None = None) -> int:
    """标注一轮，返回写入的事件数。"""
    if not llm.enabled():
        return 0
    now = now or datetime.now(timezone.utc)
    ids = _pending(db, now)
    if not ids:
        return 0
    groups, _ = _load(db, ids)
    events = [_build_event(groups[i]) for i in ids if i in groups]  # 新的优先进批
    sector_names = list(db.scalars(select(SectorInfo.name).order_by(SectorInfo.code)))
    model = llm.model_name() or ""
    written = 0
    for k in range(0, len(events), BATCH):
        batch = sorted(events[k:k + BATCH], key=lambda e: e["id"])  # 批内按时间正序，便于指认更早的条目
        anchors = _anchors(db, batch)
        try:
            result = classify_batch(batch, sector_names, anchors)
        except Exception as exc:  # noqa: BLE001 — 整批失败不写，下一轮重试
            logger.warning("news classify failed: %s", exc)
            break
        written += _write(db, batch, result, model)
    return written


def _write(db: Session, batch: list[dict], result: dict[int, dict], model: str) -> int:
    pub = {e["id"]: e["pub_at"] for e in batch}
    rows: dict[int, NewsEvent] = {}
    root: dict[int, int] = {}  # 本批 event_id → 根事件 id
    for e in batch:  # 时间正序：指向的本批条目一定已经处理过
        r = dict(result.get(e["id"]) or {"category": None, "score": None, "sectors": [], "parent": None})
        parent = r.pop("parent", None)
        pid = root.get(parent, parent) if parent is not None else None
        if pid is not None:
            parent_row = rows.get(pid) or db.get(NewsEvent, pid)
            parent_pub = pub.get(pid)
            if parent_pub is None and parent_row is not None:
                parent_pub = min(_iso(i.pub_at) for i in db.scalars(select(NewsItem).where(NewsItem.event_id == pid)))
            too_far = parent_pub is not None and (
                datetime.fromisoformat(e["pub_at"].replace("Z", "+00:00"))
                - datetime.fromisoformat(parent_pub.replace("Z", "+00:00")) > THREAD_WINDOW * 2
            )
            if parent_row is None or parent_row.parent_id is not None or too_far:
                pid = None
            elif r["score"] is not None and (parent_row.score or 0) < r["score"]:
                parent_row.score = r["score"]  # 整串重要度取最高：跌幅扩大到 9% 那条更重要
        row = NewsEvent(event_id=e["id"], model=model, parent_id=pid, **r)
        db.add(row)
        rows[e["id"]] = row
        root[e["id"]] = pid if pid is not None else e["id"]
    db.flush()
    for pid in {root[eid] for eid in rows if root[eid] != eid}:  # 本批挂了后续的父事件
        _update_thread_sources(db, pid, rows)
    db.commit()
    return len(batch)


def _update_thread_sources(db: Session, pid: int, rows: dict[int, NewsEvent]) -> None:
    kids = select(NewsEvent.event_id).where(NewsEvent.parent_id == pid)
    n = db.scalar(select(func.count(func.distinct(NewsItem.source)))
                  .where((NewsItem.event_id == pid) | NewsItem.event_id.in_(kids)))
    row = rows.get(pid) or db.get(NewsEvent, pid)
    if row is not None:
        row.thread_sources = n
