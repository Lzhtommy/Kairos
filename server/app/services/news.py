"""全球快讯采集：5 个中文 7×24 快讯源轮询 → 归一 → 跨源事件合并 → 落库。

源的选择依据 2026-11-20 生产 ECS 实测（10 分钟 × 51 轮，零失败、无限流）：
财联社 / 金十 / 华尔街见闻 / 新浪 7×24 / 同花顺。海外媒体 RSS 在机房多被墙，
东财、格隆汇在多源事件里明显偏慢，都不接。

这些都是网页前端用的无文档接口，字段和签名随时可能变：每个源独立熔断，
一个源挂了只跳过它，绝不能挡住其他源（财联社旧接口在 akshare 里卡 8 分钟的教训）。

重要度归一为 0 普通 / 1 重要 / 2 特别重要：
- 财联社 level：A→2，B→1，C→0
- 金十 important=1 → 1
- 华尔街见闻 score≥2 → 1（3 多为「要闻汇总」类）
- 同花顺 import≥3 → 1
- 新浪 tag 含「焦点」→ 1
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
import urllib.parse
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.news import NewsItem

logger = logging.getLogger("kairos.news")

CST = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
TIMEOUT = 8.0
PAGE = 30


@dataclass
class Item:
    source: str
    source_id: str
    pub_at: datetime  # UTC
    title: str
    content: str
    url: str | None
    importance: int


# ---------------------------------------------------------------- 文本清洗

_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<br\s*/?>|</p>", re.I)
_BOILER = re.compile(
    r"^(?:(?:财联社|金十数据|金十期货|格隆汇)\d{1,2}月\d{1,2}日(?:电|讯)?|\d{1,2}月\d{1,2}日(?:电|讯))[，,｜|：:\s]*"
)
_BRACKET_TITLE = re.compile(r"^【([^】]{2,200})】\s*")


def clean(s: str | None) -> str:
    s = _TAG.sub("", _BREAK.sub("\n", s or ""))
    s = html.unescape(s).replace("　", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\s*\n\s*", "\n", s).strip()


def split_title(title: str | None, content: str) -> tuple[str, str]:
    """正文以【标题】开头的拆出来（财联社 / 新浪 / 金十的惯例），去掉「财联社X月X日电，」这类抬头。"""
    title = clean(title)
    content = clean(content)
    m = _BRACKET_TITLE.match(content)
    if m:
        if not title:
            title = m.group(1).strip()
        content = content[m.end():]
    content = _BOILER.sub("", content).strip()
    return title[:256], content[:4000]


def _from_epoch(v) -> datetime:  # noqa: ANN001
    return datetime.fromtimestamp(int(v), tz=timezone.utc)


def _from_cst(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST).astimezone(timezone.utc)


def _item(source: str, sid, pub: datetime, title, content, url, imp: int) -> Item | None:  # noqa: ANN001
    t, c = split_title(title, content or "")
    if not (t or c):
        return None
    return Item(source, str(sid)[:40], pub, t, c, (url or None) and str(url)[:512], imp)


# ---------------------------------------------------------------- 各源解析（纯函数，便于测试）


def parse_cls(d: dict) -> list[Item]:
    out = []
    for x in d["data"]["roll_data"]:
        if x.get("is_ad"):
            continue
        imp = {"A": 2, "B": 1}.get(x.get("level") or "", 0)
        it = _item("cls", x["id"], _from_epoch(x["ctime"]), x.get("title"), x.get("content") or x.get("brief"),
                   f"https://www.cls.cn/detail/{x['id']}", imp)
        if it:
            out.append(it)
    return out


def parse_jin10(d: dict) -> list[Item]:
    out = []
    for x in d["data"]:
        if x.get("type") != 0:  # 2 = 长文章链接，不是快讯
            continue
        data = x.get("data") or {}
        it = _item("jin10", x["id"], _from_cst(x["time"]), data.get("title"), data.get("content"),
                   f"https://flash.jin10.com/detail/{x['id']}", 1 if x.get("important") else 0)
        if it:
            out.append(it)
    return out


def parse_wscn(d: dict) -> list[Item]:
    out = []
    for x in d["data"]["items"]:
        imp = 1 if (x.get("score") or 0) >= 2 else 0
        it = _item("wscn", x["id"], _from_epoch(x["display_time"]), x.get("title"),
                   x.get("content_text") or x.get("content"), x.get("uri"), imp)
        if it:
            out.append(it)
    return out


def parse_sina(d: dict) -> list[Item]:
    out = []
    for x in d["result"]["data"]["feed"]["list"]:
        tags = {t.get("name") for t in x.get("tag") or [] if isinstance(t, dict)}
        url = x.get("docurl")
        try:
            ext = json.loads(x["ext"]) if isinstance(x.get("ext"), str) else (x.get("ext") or {})
            url = ext.get("docurl") or url
        except (ValueError, AttributeError):
            pass
        it = _item("sina", x["id"], _from_cst(x["create_time"]), None, x.get("rich_text"), url,
                   1 if "焦点" in tags else 0)
        if it:
            out.append(it)
    return out


def parse_ths(d: dict) -> list[Item]:
    out = []
    for x in d["data"]["list"]:
        try:
            imp = 1 if int(x.get("import") or 0) >= 3 else 0
        except (TypeError, ValueError):
            imp = 0
        it = _item("ths", x["id"], _from_epoch(x["ctime"]), x.get("title"), x.get("digest"), x.get("url"), imp)
        if it:
            out.append(it)
    return out


# ---------------------------------------------------------------- 各源抓取


def cls_sign(params: dict[str, str]) -> str:
    """财联社 web 端签名：按 key 排序 urlencode 后 sha1，再 md5。"""
    q = urllib.parse.urlencode(sorted(params.items()))
    return hashlib.md5(hashlib.sha1(q.encode()).hexdigest().encode()).hexdigest()  # noqa: S324


def _get(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    with httpx.Client(timeout=TIMEOUT, trust_env=False, headers={"User-Agent": UA, **(headers or {})}) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json()


def fetch_cls() -> dict:
    now = str(int(time.time()))
    p = {"app": "CailianpressWeb", "os": "web", "sv": "8.4.6", "category": "", "lastTime": now,
         "last_time": now, "refresh_type": "1", "rn": str(PAGE)}
    p["sign"] = cls_sign(p)
    d = _get("https://www.cls.cn/v1/roll/get_roll_list", p, {"Referer": "https://www.cls.cn/telegraph"})
    if d.get("errno") != 0:
        raise ValueError(f"cls errno={d.get('errno')} {d.get('msg')}")
    return d


def fetch_jin10() -> dict:
    return _get(
        "https://flash-api.jin10.com/get_flash_list",
        {"channel": "-8200", "vip": "1"},
        {"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0", "Origin": "https://www.jin10.com",
         "Referer": "https://www.jin10.com/"},
    )


def fetch_wscn() -> dict:
    return _get("https://api-one-wscn.awtmt.com/apiv1/content/lives",
                {"channel": "global-channel", "limit": str(PAGE)})


def fetch_sina() -> dict:
    return _get("https://zhibo.sina.com.cn/api/zhibo/feed",
                {"page": "1", "page_size": str(PAGE), "zhibo_id": "152"})


def fetch_ths() -> dict:
    return _get("https://news.10jqka.com.cn/tapp/news/push/stock/",
                {"page": "1", "tag": "", "track": "website", "pagesize": str(PAGE)})


SOURCES: dict[str, Callable[[], list[Item]]] = {
    "cls": lambda: parse_cls(fetch_cls()),
    "jin10": lambda: parse_jin10(fetch_jin10()),
    "wscn": lambda: parse_wscn(fetch_wscn()),
    "sina": lambda: parse_sina(fetch_sina()),
    "ths": lambda: parse_ths(fetch_ths()),
}


# ---------------------------------------------------------------- 跨源事件合并

_NON_WORD = re.compile(r"[^0-9A-Za-z一-鿿]")
KEY_CHARS = 80
MIN_GRAMS = 6
MATCH_RATIO = 0.5
EVENT_WINDOW = timedelta(minutes=10)  # 实测同一事件各源最多差约 4 分钟
INDEX_KEEP = timedelta(hours=2)


def grams(title: str, content: str) -> frozenset[str]:
    """标题 + 正文去标点后前 80 字的字符 bigram。"""
    t = _NON_WORD.sub("", _BOILER.sub("", f"{title}{content}"))[:KEY_CHARS]
    return frozenset(t[i:i + 2] for i in range(len(t) - 1))


def _similar(a: frozenset[str], b: frozenset[str]) -> float:
    n = min(len(a), len(b))
    return len(a & b) / n if n >= MIN_GRAMS else 0.0


@dataclass
class _Event:
    event_id: int
    pub_at: datetime
    sources: set[str] = field(default_factory=set)
    members: list[frozenset[str]] = field(default_factory=list)


class EventIndex:
    """最近两小时的事件，用于把不同源对同一件事的报道归到一个 event_id。

    同一事件里每个源最多一条：同源的后续报道（如「跌幅扩大至 5%」→「收盘跌 5.1%」）
    是新进展，单独成事件。
    """

    def __init__(self) -> None:
        self._events: list[_Event] = []

    def match(self, source: str, pub_at: datetime, g: frozenset[str]) -> int | None:
        best, best_score = None, MATCH_RATIO
        for e in self._events:
            if source in e.sources or abs(pub_at - e.pub_at) > EVENT_WINDOW:
                continue
            score = max(_similar(g, m) for m in e.members)
            if score >= best_score:
                best, best_score = e, score
        return best.event_id if best else None

    def add(self, event_id: int, source: str, pub_at: datetime, g: frozenset[str]) -> None:
        for e in self._events:
            if e.event_id == event_id:
                e.sources.add(source)
                e.members.append(g)
                e.pub_at = min(e.pub_at, pub_at)
                return
        self._events.append(_Event(event_id, pub_at, {source}, [g]))

    def prune(self, now: datetime) -> None:
        self._events = [e for e in self._events if now - e.pub_at <= INDEX_KEEP]

    def __len__(self) -> int:
        return len(self._events)


# ---------------------------------------------------------------- 熔断


@dataclass
class _Health:
    fails: int = 0
    until: float = 0.0


FAILS_BEFORE_COOLDOWN = 3
COOLDOWN_BASE = 60.0
COOLDOWN_MAX = 1800.0


# ---------------------------------------------------------------- 采集器


def _utc(dt: datetime) -> datetime:
    """SQLite 读回来的 DateTime 不带时区，按 UTC 补上。"""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Collector:
    SEEN_KEEP = 2000  # 每个源记住最近多少个 id（接口每次只返回 30 条，足够）

    def __init__(self, sources: dict[str, Callable[[], list[Item]]] | None = None) -> None:
        self.sources = sources if sources is not None else SOURCES
        self.index = EventIndex()
        self.seen: dict[str, dict[str, None]] = {s: {} for s in self.sources}
        self.health: dict[str, _Health] = {s: _Health() for s in self.sources}
        self.primed = False

    def _remember(self, source: str, sid: str) -> None:
        seen = self.seen.setdefault(source, {})
        seen[sid] = None
        while len(seen) > self.SEEN_KEEP:
            del seen[next(iter(seen))]

    def prime(self, db: Session, now: datetime) -> None:
        """重启后从库里恢复已见 id 和近两小时事件，避免重复入库、事件断链。"""
        self.index = EventIndex()
        self.seen = {s: {} for s in self.sources}
        rows = db.execute(
            select(NewsItem.source, NewsItem.source_id, NewsItem.pub_at, NewsItem.title, NewsItem.content,
                   NewsItem.event_id)
            .where(NewsItem.seen_at >= now - timedelta(days=2))
            .order_by(NewsItem.id)
        ).all()
        for source, sid, pub_at, title, content, event_id in rows:
            self._remember(source, sid)
            pub_at = _utc(pub_at)
            if event_id is not None and now - pub_at <= INDEX_KEEP:
                self.index.add(event_id, source, pub_at, grams(title, content))
        self.primed = True

    def _due(self, now_ts: float) -> list[str]:
        return [s for s in self.sources if self.health[s].until <= now_ts]

    def _record(self, source: str, err: Exception | None, now_ts: float) -> None:
        h = self.health[source]
        if err is None:
            if h.fails >= FAILS_BEFORE_COOLDOWN:
                logger.info("news source %s recovered after %d failures", source, h.fails)
            h.fails, h.until = 0, 0.0
            return
        h.fails += 1
        if h.fails == 1:
            logger.warning("news source %s failed: %r", source, err)
        if h.fails >= FAILS_BEFORE_COOLDOWN:
            wait = min(COOLDOWN_BASE * 2 ** (h.fails - FAILS_BEFORE_COOLDOWN), COOLDOWN_MAX)
            h.until = now_ts + wait
            logger.warning("news source %s failed %d times in a row, cooling down %ds: %r",
                           source, h.fails, wait, err)

    def fetch(self, now_ts: float) -> list[Item]:
        due = self._due(now_ts)
        if not due:
            return []
        items: list[Item] = []
        with ThreadPoolExecutor(max_workers=len(due)) as pool:
            futures = {s: pool.submit(self.sources[s]) for s in due}
        for s, fut in futures.items():
            try:
                got = fut.result()
            except Exception as exc:  # noqa: BLE001 — 单源失败只熔断这个源
                self._record(s, exc, now_ts)
                continue
            if not got:  # 接口没报错但一条都解析不出来，多半是字段改了
                self._record(s, ValueError("no items parsed"), now_ts)
                continue
            self._record(s, None, now_ts)
            items.extend(got)
        return items

    def run(self, db: Session, now: datetime | None = None) -> int:
        """抓一轮并入库，返回新入库条数。"""
        now = now or datetime.now(timezone.utc)
        if not self.primed:
            self.prime(db, now)
        items = self.fetch(now.timestamp())
        fresh = [it for it in items if it.source_id not in self.seen.get(it.source, {})]
        # 同一轮里同源可能重复返回同一条（翻页边界），按 (source, id) 去一次
        uniq: dict[tuple[str, str], Item] = {}
        for it in fresh:
            uniq.setdefault((it.source, it.source_id), it)
        fresh = sorted(uniq.values(), key=lambda it: it.pub_at)
        self.index.prune(now)
        if not fresh:
            return 0
        try:
            for it in fresh:
                g = grams(it.title, it.content)
                ev = self.index.match(it.source, it.pub_at, g)
                row = NewsItem(
                    source=it.source, source_id=it.source_id, pub_at=it.pub_at, seen_at=now, title=it.title,
                    content=it.content, url=it.url, importance=it.importance, event_id=ev,
                )
                db.add(row)
                if ev is None:
                    db.flush()
                    row.event_id = ev = row.id
                self.index.add(ev, it.source, it.pub_at, g)
            db.commit()
        except Exception:
            db.rollback()
            self.primed = False  # 内存索引可能已和库不一致，下一轮从库重建
            raise
        for it in fresh:
            self._remember(it.source, it.source_id)
        return len(fresh)


collector = Collector()


# ---------------------------------------------------------------- 查询（按事件聚合）

SOURCE_NAMES = {"cls": "财联社", "jin10": "金十数据", "wscn": "华尔街见闻", "sina": "新浪财经", "ths": "同花顺"}
MULTI_SOURCE = 3  # 3 家及以上同时报道的事件视为重要


def _iso(dt: datetime) -> str:
    return _utc(dt).isoformat().replace("+00:00", "Z")


def _build_event(items: list[NewsItem]) -> dict:
    items = sorted(items, key=lambda r: _utc(r.pub_at))
    # 正文取信息量最多的一条：重要度高 > 有标题 > 正文长
    main = max(items, key=lambda r: (r.importance, bool(r.title), len(r.content)))
    title = main.title or next((r.title for r in items if r.title), "")
    n_sources = len({r.source for r in items})
    importance = max(r.importance for r in items)
    level = max(importance, 1 if n_sources >= MULTI_SOURCE else 0)
    return {
        "id": items[0].event_id,
        "pub_at": _iso(items[0].pub_at),
        "level": level,
        "title": title,
        "content": main.content,
        "sources": [
            {"source": r.source, "name": SOURCE_NAMES.get(r.source, r.source), "url": r.url,
             "pub_at": _iso(r.pub_at), "importance": r.importance}
            for r in items
        ],
    }


def _important_having():  # noqa: ANN202
    return or_(func.max(NewsItem.importance) >= 1, func.count(func.distinct(NewsItem.source)) >= MULTI_SOURCE)


def list_events(db: Session, *, limit: int = 50, before: int | None = None, important: bool = False,
                q: str | None = None, now: datetime | None = None) -> list[dict]:
    """最新事件，按 event_id 倒序（≈ 首次报道时间倒序），before 为翻页游标。"""
    ev = NewsItem.event_id
    stmt = select(ev).where(ev.is_not(None))
    if before is not None:
        stmt = stmt.where(ev < before)
    if q:
        # 搜索只看近 30 天，避免对全表 LIKE
        since = (now or datetime.now(timezone.utc)) - timedelta(days=30)
        hit = select(NewsItem.event_id).where(
            NewsItem.seen_at >= since,
            or_(NewsItem.title.contains(q, autoescape=True), NewsItem.content.contains(q, autoescape=True)),
        )
        stmt = stmt.where(ev.in_(hit))
    stmt = stmt.group_by(ev)
    if important:
        stmt = stmt.having(_important_having())
    ids = list(db.scalars(stmt.order_by(ev.desc()).limit(limit)))
    if not ids:
        return []
    groups: dict[int, list[NewsItem]] = {}
    for r in db.scalars(select(NewsItem).where(ev.in_(ids))):
        groups.setdefault(r.event_id, []).append(r)
    return [_build_event(groups[i]) for i in ids if i in groups]


def recent_headlines(db: Session, *, hours: int = 24, limit: int = 120, now: datetime | None = None) -> list[str]:
    """叙事打分用：近 N 小时的事件，每个事件一条标题。

    超过 limit 时优先留重要度高、报道家数多的，再按时间正序排给 LLM。
    """
    now = now or datetime.now(timezone.utc)
    rows = db.scalars(
        select(NewsItem).where(NewsItem.pub_at >= now - timedelta(hours=hours), NewsItem.event_id.is_not(None))
    ).all()
    groups: dict[int, list[NewsItem]] = {}
    for r in rows:
        groups.setdefault(r.event_id, []).append(r)
    events = [_build_event(g) for g in groups.values()]
    events.sort(key=lambda e: (e["level"], len(e["sources"]), e["pub_at"]), reverse=True)
    picked = sorted(events[:limit], key=lambda e: e["pub_at"])
    out = []
    for e in picked:
        text = e["title"] or e["content"][:100]
        if text:
            out.append(text)
    return out


def source_status(db: Session, now: datetime | None = None) -> list[dict]:
    """各源最近一次入库时间、近 24 小时条数和当前熔断状态。"""
    now = now or datetime.now(timezone.utc)
    stats = {
        s: (last, n)
        for s, last, n in db.execute(
            select(NewsItem.source, func.max(NewsItem.seen_at), func.count())
            .where(NewsItem.seen_at >= now - timedelta(hours=24))
            .group_by(NewsItem.source)
        )
    }
    out = []
    for s in collector.sources:
        last, n = stats.get(s, (None, 0))
        h = collector.health.get(s)
        out.append({
            "source": s, "name": SOURCE_NAMES.get(s, s), "last_seen": _iso(last) if last else None,
            "count_24h": n, "failing": bool(h and h.fails > 0),
        })
    return out
