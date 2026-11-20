"""全球快讯采集：各源解析、跨源事件合并、熔断、入库去重。不碰网络。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.news import NewsItem
from app.services import news
from app.services.news import Collector, EventIndex, Item, grams

UTC = timezone.utc
T0 = datetime(2026, 11, 20, 6, 50, tzinfo=UTC)  # 14:50 CST


# ---------------------------------------------------------------- 解析


def test_parse_cls_splits_bracket_title_and_maps_level():
    d = {"errno": 0, "data": {"roll_data": [
        {"id": 2539968, "ctime": int(T0.timestamp()), "level": "B", "title": "日韩股市收盘均跌超5%",
         "content": "【日韩股市收盘均跌超5%】财联社11月20日电，韩国KOSPI指数收跌5.21%。"},
        {"id": 1, "ctime": int(T0.timestamp()), "level": "A", "title": "", "content": "财联社11月20日电，重磅。"},
        {"id": 2, "ctime": int(T0.timestamp()), "level": "C", "is_ad": 1, "title": "广告", "content": "广告"},
    ]}}
    a, b = news.parse_cls(d)
    assert (a.source, a.source_id, a.title, a.content, a.importance) == (
        "cls", "2539968", "日韩股市收盘均跌超5%", "韩国KOSPI指数收跌5.21%。", 1)
    assert a.url == "https://www.cls.cn/detail/2539968"
    assert a.pub_at == T0
    assert (b.title, b.content, b.importance) == ("", "重磅。", 2)


def test_parse_jin10_skips_articles_and_converts_cst():
    d = {"data": [
        {"id": "20261120145255122800", "type": 0, "time": "2026-11-20 14:52:55", "important": 1,
         "data": {"title": "", "content": "<b>美国地质勘探局：加州发生5.4级地震。</b>"}},
        {"id": "x", "type": 2, "time": "2026-11-20 14:52:55", "important": 1,
         "data": {"content": "长文章", "link": "https://xnews.jin10.com/details/1"}},
        {"id": "empty", "type": 0, "time": "2026-11-20 14:52:55", "important": 0, "data": {"content": ""}},
    ]}
    (it,) = news.parse_jin10(d)
    assert it.content == "美国地质勘探局：加州发生5.4级地震。"
    assert it.importance == 1
    assert it.pub_at == datetime(2026, 11, 20, 6, 52, 55, tzinfo=UTC)


def test_parse_wscn_sina_ths():
    w = news.parse_wscn({"data": {"items": [
        {"id": 3203022, "display_time": int(T0.timestamp()), "score": 2, "title": "",
         "content": "<p>日经225指数跌幅扩大至5%。</p>", "content_text": "日经225指数跌幅扩大至5%。",
         "uri": "https://wallstreetcn.com/livenews/3203022"},
        {"id": 3203023, "display_time": int(T0.timestamp()), "score": 1, "title": "t", "content": "<p>c</p>",
         "uri": "u"},
    ]}})
    assert [(x.importance, x.content) for x in w] == [(1, "日经225指数跌幅扩大至5%。"), (0, "c")]

    s = news.parse_sina({"result": {"data": {"feed": {"list": [
        {"id": 5227251, "create_time": "2026-11-20 14:52:44", "rich_text": "【标题A】正文A",
         "tag": [{"id": "1", "name": "市场"}, {"id": "2", "name": "焦点"}],
         "docurl": "https://m", "ext": json.dumps({"docurl": "https://finance.sina.com.cn/x.shtml"})},
        {"id": 5227252, "create_time": "2026-11-20 14:52:44", "rich_text": "无标题", "tag": [], "ext": "bad"},
    ]}}}})
    assert [(x.title, x.content, x.importance, x.url) for x in s] == [
        ("标题A", "正文A", 1, "https://finance.sina.com.cn/x.shtml"), ("", "无标题", 0, None)]

    t = news.parse_ths({"data": {"list": [
        {"id": "5721443", "ctime": str(int(T0.timestamp())), "title": "日韩股市集体收跌", "digest": "摘要",
         "url": "https://news.10jqka.com.cn/c.shtml", "import": "3"},
        {"id": "5721444", "ctime": str(int(T0.timestamp())), "title": "t", "digest": "", "url": "", "import": "0"},
    ]}})
    assert [(x.title, x.content, x.importance, x.url) for x in t] == [
        ("日韩股市集体收跌", "摘要", 1, "https://news.10jqka.com.cn/c.shtml"), ("t", "", 0, None)]


def test_cls_sign_is_md5_of_sha1_of_sorted_query():
    p = {"b": "2", "a": "1"}
    expect = hashlib.md5(hashlib.sha1(b"a=1&b=2").hexdigest().encode()).hexdigest()
    assert news.cls_sign(p) == expect


# ---------------------------------------------------------------- 事件合并


def _g(text: str) -> frozenset[str]:
    return grams("", text)


def test_event_index_merges_cross_source_reports():
    idx = EventIndex()
    idx.add(1, "wscn", T0, _g("德国10月PPI同比 8.1%，预期 7.3%，前值 6.7%。"))
    # 金十 2 秒后同一数据，带抬头
    assert idx.match("jin10", T0 + timedelta(seconds=2), _g("金十数据11月20日讯，德国10月PPI同比8.1%，预期7.3%，前值6.7%")) == 1
    # 同源不并（同一源的后续报道算新进展）
    assert idx.match("wscn", T0, _g("德国10月PPI同比 8.1%，预期 7.3%，前值 6.7%。")) is None
    # 超出时间窗不并
    assert idx.match("jin10", T0 + timedelta(hours=1), _g("德国10月PPI同比 8.1%，预期 7.3%，前值 6.7%。")) is None
    # 无关新闻不并
    assert idx.match("jin10", T0, _g("美国地质勘探局：加利福尼亚州索莱达地区发生5.4级地震")) is None


def test_event_index_ignores_too_short_text():
    idx = EventIndex()
    idx.add(1, "cls", T0, _g("快讯"))
    assert idx.match("sina", T0, _g("快讯")) is None


def test_event_index_prunes_old_events():
    idx = EventIndex()
    idx.add(1, "cls", T0, _g("日韩股市收盘均跌超5%，韩国KOSPI指数收跌5.21%"))
    idx.prune(T0 + timedelta(hours=3))
    assert len(idx) == 0


# ---------------------------------------------------------------- 采集器


def _item(source: str, sid: str, text: str, pub: datetime = T0, imp: int = 0) -> Item:
    return Item(source, sid, pub, "", text, None, imp)


class Feed:
    """可变的假源：items 为当前接口返回，error 非空时抛错。"""

    def __init__(self, *items: Item) -> None:
        self.items = list(items)
        self.error: Exception | None = None
        self.calls = 0

    def __call__(self) -> list[Item]:
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.items)


PPI = "德国10月PPI同比 8.1%，预期 7.3%，前值 6.7%。"
QUAKE = "美国地质勘探局：加利福尼亚州索莱达地区发生5.4级地震，震源深度9.0公里。"


def test_collector_inserts_dedups_and_groups_events(db):
    a, b = Feed(_item("a", "1", PPI), _item("a", "2", QUAKE)), Feed(_item("b", "x", PPI, T0 + timedelta(seconds=3)))
    c = Collector({"a": a, "b": b})
    assert c.run(db, T0) == 3
    assert c.run(db, T0 + timedelta(seconds=15)) == 0  # 同样的返回不再入库

    rows = db.execute(select(NewsItem).order_by(NewsItem.id)).scalars().all()
    by = {(r.source, r.source_id): r for r in rows}
    assert by[("a", "1")].event_id == by[("b", "x")].event_id == by[("a", "1")].id
    assert by[("a", "2")].event_id == by[("a", "2")].id
    assert all(r.seen_at.replace(tzinfo=UTC) == T0 for r in rows)


def test_collector_primes_from_db_after_restart(db):
    a = Feed(_item("a", "1", PPI))
    Collector({"a": a, "b": Feed()}).run(db, T0)
    # 重启：新实例从库恢复已见 id 和事件索引
    b = Feed(_item("b", "x", PPI, T0 + timedelta(seconds=5)))
    c2 = Collector({"a": a, "b": b})
    assert c2.run(db, T0 + timedelta(seconds=30)) == 1
    rows = db.execute(select(NewsItem).order_by(NewsItem.id)).scalars().all()
    assert len(rows) == 2 and rows[0].event_id == rows[1].event_id


def test_collector_circuit_breaker_isolates_failing_source(db):
    good, bad = Feed(_item("good", "1", QUAKE)), Feed()
    bad.error = RuntimeError("boom")
    c = Collector({"good": good, "bad": bad})
    t = T0
    for _ in range(news.FAILS_BEFORE_COOLDOWN):
        c.run(db, t)
        t += timedelta(seconds=15)
    assert bad.calls == news.FAILS_BEFORE_COOLDOWN
    c.run(db, t)  # 冷却中，不再请求坏源，好源照常
    assert bad.calls == news.FAILS_BEFORE_COOLDOWN
    assert good.calls == news.FAILS_BEFORE_COOLDOWN + 1
    # 冷却结束且恢复后，失败计数清零
    bad.error = None
    bad.items = [_item("bad", "9", PPI)]
    c.run(db, t + timedelta(seconds=news.COOLDOWN_BASE + 1))
    assert c.health["bad"].fails == 0
    assert db.execute(select(NewsItem).where(NewsItem.source == "bad")).scalar_one().source_id == "9"


def test_collector_treats_empty_parse_as_failure(db):
    empty = Feed()
    c = Collector({"e": empty})
    c.run(db, T0)
    assert c.health["e"].fails == 1


# ---------------------------------------------------------------- 查询 / API / 叙事


def _seed(db, t=T0):
    """事件 1：PPI 三家报道；事件 2：地震一家、重要；事件 3：普通一家。"""
    feeds = {
        "a": Feed(_item("a", "1", PPI, t), _item("a", "2", QUAKE, t + timedelta(seconds=30), imp=1)),
        "b": Feed(_item("b", "1", "德国10月PPI同比8.1%，预期7.3%，前值6.7%，高于预期。", t + timedelta(seconds=2))),
        "c": Feed(_item("c", "1", PPI, t + timedelta(seconds=40)),
                  _item("c", "2", "斯里兰卡央行行长：尚未对明年的经济增长做出预测。", t + timedelta(minutes=1))),
    }
    Collector(feeds).run(db, t + timedelta(minutes=2))


def test_list_events_groups_and_paginates(db):
    _seed(db)
    evs = news.list_events(db)
    assert [len(e["sources"]) for e in evs] == [1, 1, 3]  # 新的在前
    ppi = evs[-1]
    assert ppi["level"] == 1  # 三家同报算重要
    assert [s["source"] for s in ppi["sources"]] == ["a", "b", "c"]
    assert ppi["pub_at"] == "2026-11-20T06:50:00Z"

    page1 = news.list_events(db, limit=2)
    page2 = news.list_events(db, limit=2, before=page1[-1]["id"])
    assert [e["id"] for e in page1 + page2] == [e["id"] for e in evs]

    assert {e["content"][:4] for e in news.list_events(db, important=True)} == {"美国地质", "德国10"}
    (hit,) = news.list_events(db, q="地震", now=T0)
    assert hit["level"] == 1


def test_recent_headlines_prefers_important_then_chronological(db):
    _seed(db)
    now = T0 + timedelta(hours=1)
    all3 = news.recent_headlines(db, now=now)
    assert len(all3) == 3 and all3[-1].startswith("斯里兰卡")
    top2 = news.recent_headlines(db, limit=2, now=now)
    assert [h[:4] for h in top2] == ["德国10", "美国地质"]
    assert news.recent_headlines(db, now=T0 + timedelta(days=2)) == []


def test_narrative_reads_news_item_and_falls_back(db, monkeypatch):
    from app.services import narrative

    monkeypatch.setattr(narrative, "MIN_DB_HEADLINES", 3)
    monkeypatch.setattr(news, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: T0 + timedelta(hours=1))}))
    _seed(db)
    assert len(narrative.fetch_headlines(db)) == 3

    monkeypatch.setattr(narrative, "MIN_DB_HEADLINES", 10)
    import sys
    import types

    class DF:
        columns = ["标题"]

        def __getitem__(self, _k):
            return types.SimpleNamespace(tolist=lambda: ["东财 A", "东财 A", "东财 B"])

    monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(stock_info_global_em=lambda: DF()))
    assert narrative.fetch_headlines(db) == ["东财 A", "东财 B"]


def test_news_api_requires_login_and_returns_events(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.news import router
    from app.core.db import get_db
    from app.core.security import get_current_user

    _seed(db)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    assert TestClient(app).get("/news/events").status_code == 401

    app.dependency_overrides[get_current_user] = lambda: object()
    c = TestClient(app)
    body = c.get("/news/events", params={"limit": 2}).json()
    assert len(body["events"]) == 2 and body["next"] == body["events"][-1]["id"]
    assert c.get("/news/events", params={"before": body["next"]}).json()["next"] is None
    srcs = c.get("/news/sources").json()
    assert [s["source"] for s in srcs] == ["cls", "jin10", "wscn", "sina", "ths"]
