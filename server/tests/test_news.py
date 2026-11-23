"""全球快讯采集：各源解析、跨源事件合并、熔断、入库去重。不碰网络。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

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

from app.models.news import NewsEvent  # noqa: E402
from app.models.sector import SectorInfo  # noqa: E402
from app.services import news_classify  # noqa: E402


def _seed(db, t=T0):
    """事件 1：PPI 三家报道；事件 2：地震一家、自标重要；事件 3：普通一家；事件 4：新浪独家公告。"""
    feeds = {
        "a": Feed(_item("a", "1", PPI, t), _item("a", "2", QUAKE, t + timedelta(seconds=30), imp=1)),
        "b": Feed(_item("b", "1", "德国10月PPI同比8.1%，预期7.3%，前值6.7%，高于预期。", t + timedelta(seconds=2))),
        "c": Feed(_item("c", "1", PPI, t + timedelta(seconds=40)),
                  _item("c", "2", "斯里兰卡央行行长：尚未对明年的经济增长做出预测。", t + timedelta(minutes=1))),
        "sina": Feed(_item("sina", "9", "ST联光：聘任牛泽元为公司总裁，任期三年，自董事会审议通过之日起。",
                           t + timedelta(minutes=1, seconds=30))),
    }
    Collector(feeds).run(db, t + timedelta(minutes=2))
    return {e["content"][:4]: e["id"] for e in news.list_events(db, view="all", limit=200)} | {
        "ST联光": db.execute(select(NewsItem.event_id).where(NewsItem.source == "sina")).scalar_one()}


def test_list_events_all_view_groups_paginates_and_hides_sina_noise(db):
    ids = _seed(db)
    evs = news.list_events(db, view="all")
    assert [len(e["sources"]) for e in evs] == [1, 1, 3]  # 新的在前，新浪独家公告不出现
    ppi = evs[-1]
    assert [s["source"] for s in ppi["sources"]] == ["a", "b", "c"]
    assert ppi["pub_at"] == "2026-11-20T06:50:00Z"

    page1 = news.list_events(db, view="all", limit=2)
    page2 = news.list_events(db, view="all", limit=2, before=page1[-1]["id"])
    assert [e["id"] for e in page1 + page2] == [e["id"] for e in evs]

    # LLM 认为重要的新浪独家照样出现
    db.add(NewsEvent(event_id=ids["ST联光"], category="公司个股", score=4, sectors=[]))
    db.commit()
    assert ids["ST联光"] in [e["id"] for e in news.list_events(db, view="all")]

    (hit,) = news.list_events(db, view="all", q="地震", now=T0)
    assert hit["content"].startswith("美国地质")


def test_featured_view_uses_llm_score_then_fallback_rule(db):
    ids = _seed(db)
    # 未打标：三家同报的 PPI 进要闻；单家自标重要的地震不进
    assert [e["id"] for e in news.list_events(db)] == [ids["德国10"]]

    db.add_all([
        NewsEvent(event_id=ids["美国地质"], category="其他", score=4, sectors=[]),
        NewsEvent(event_id=ids["德国10"], category="宏观数据", score=3, sectors=["电子"]),
    ])
    db.commit()
    # 地震 4 分进；PPI 3 分但三家同报，也进
    assert [e["id"] for e in news.list_events(db)] == [ids["美国地质"], ids["德国10"]]
    db.get(NewsEvent, ids["德国10"]).score = 2
    db.commit()
    evs = news.list_events(db)
    assert [e["id"] for e in evs] == [ids["美国地质"]]  # LLM 分数优先于源标记规则
    assert evs[0]["featured"] and evs[0]["score"] == 4 and evs[0]["category"] == "其他"

    (macro,) = news.list_events(db, view="all", category="宏观数据")
    assert macro["sectors"] == ["电子"] and not macro["featured"] and macro["score"] == 2


def test_llm_featured_rule():
    assert news.llm_featured(4, 1)
    assert news.llm_featured(3, 3)
    assert not news.llm_featured(3, 2)
    assert not news.llm_featured(2, 5)


def test_fallback_featured_rule():
    assert news.fallback_featured(2, 1)        # 财联社 A 级
    assert news.fallback_featured(0, 3)        # 三家同报
    assert news.fallback_featured(1, 2)        # 两家且有一家标重要
    assert not news.fallback_featured(1, 1)    # 单家自标重要不算
    assert not news.fallback_featured(0, 2)


def test_parse_keeps_only_a_share_stocks():
    (it,) = news.parse_ths({"data": {"list": [{
        "id": "1", "ctime": str(int(T0.timestamp())), "title": "t", "digest": "d", "url": "", "import": "0",
        "stock": [{"name": "周大福", "stockCode": "HK1929"}, {"name": "拓荆科技", "stockCode": "688072"},
                  {"name": "英伟达", "stockCode": "NVDA"}],
    }]}})
    assert it.stocks == [{"code": "688072", "name": "拓荆科技"}]
    (c,) = news.parse_cls({"data": {"roll_data": [{
        "id": 1, "ctime": int(T0.timestamp()), "level": "C", "title": "t", "content": "c",
        "stock_list": [{"StockID": "sh600160", "name": "巨化股份"}, {"StockID": "hk00700", "name": "腾讯"}],
    }]}})
    assert c.stocks == [{"code": "600160", "name": "巨化股份"}]


def test_recent_headlines_prefers_llm_score(db):
    ids = _seed(db)
    now = T0 + timedelta(hours=1)
    assert len(news.recent_headlines(db, now=now)) == 4
    # 未打标时三家同报的 PPI 排第一
    assert news.recent_headlines(db, limit=1, now=now)[0].startswith("德国10")
    db.add(NewsEvent(event_id=ids["斯里兰卡"], category="海外市场", score=5, sectors=[]))
    db.commit()
    top2 = news.recent_headlines(db, limit=2, now=now)
    assert [h[:3] for h in top2] == ["德国1", "斯里兰"]  # 按时间正序
    assert news.recent_headlines(db, now=T0 + timedelta(days=2)) == []


# ---------------------------------------------------------------- LLM 标注


def _fake_llm(monkeypatch, reply):
    calls = []

    def chat_json(messages, **_kw):
        calls.append(messages[0]["content"])
        if isinstance(reply, Exception):
            raise reply
        return (reply(messages[0]["content"]) if callable(reply) else reply), 100

    monkeypatch.setattr(news_classify.llm, "enabled", lambda: True)
    monkeypatch.setattr(news_classify.llm, "model_name", lambda: "fake")
    monkeypatch.setattr(news_classify.llm, "chat_json", chat_json)
    return calls


def test_classify_pending_writes_sanitized_rows_and_skips_done(db, monkeypatch):
    ids = _seed(db)
    db.add(SectorInfo(code="801080", name="电子", source="sw"))
    db.commit()
    # 批内按时间正序：0 PPI、1 地震、2 斯里兰卡、3 新浪公告；故意漏掉 2，给 1 非法分类和分数
    calls = _fake_llm(monkeypatch, {"items": [
        {"i": 3, "c": "公司个股", "s": 2, "sec": []},
        {"i": 1, "c": "瞎编", "s": 9, "sec": ["电子", "不存在"]},
        {"i": 0, "c": "宏观数据", "s": "4", "sec": ["电子"]},
        {"i": 99, "c": "其他", "s": 1},
    ]})
    now = T0 + timedelta(minutes=3)
    assert news_classify.classify_pending(db, now) == 4
    assert "电子" in calls[0] and "德国10月PPI" in calls[0]

    rows = {r.event_id: r for r in db.scalars(select(NewsEvent))}
    assert (rows[ids["德国10"]].category, rows[ids["德国10"]].score, rows[ids["德国10"]].sectors) == ("宏观数据", 4, ["电子"])
    assert (rows[ids["美国地质"]].category, rows[ids["美国地质"]].score, rows[ids["美国地质"]].sectors) == (None, 5, ["电子"])
    assert (rows[ids["斯里兰卡"]].category, rows[ids["斯里兰卡"]].score) == (None, None)  # 漏掉的写空行
    assert news_classify.classify_pending(db, now) == 0 and len(calls) == 1  # 已标注的不再送


def _thread_seed(db, t=T0):
    """库克同一场讲话三句（同一源、各自成事件）+ 一条无关新闻。"""
    a = Feed(
        _item("a", "1", "美联储理事库克：高通胀并未给美国央行留出耐心等待的空间。", t),
        _item("a", "2", "美联储理事库克：嵌入式通胀将要求美联储采取更激进的应对措施。", t + timedelta(minutes=1)),
        _item("a", "3", "美国地质勘探局：加利福尼亚州索莱达地区发生5.4级地震。", t + timedelta(minutes=2)),
    )
    Collector({"a": a}).run(db, t + timedelta(minutes=3))
    return sorted(db.scalars(select(NewsItem.event_id).distinct()))


def test_classify_threads_followups_within_batch_and_raises_parent_score(db, monkeypatch):
    e1, e2, e3 = _thread_seed(db)
    calls = _fake_llm(monkeypatch, {"items": [
        {"i": 0, "c": "央行货币", "s": 3},
        {"i": 1, "c": "央行货币", "s": 4, "p": 0},
        {"i": 2, "c": "其他", "s": 2, "p": 2},  # 指向自己 / 更晚的条目：忽略
    ]})
    news_classify.classify_pending(db, T0 + timedelta(minutes=4))
    assert "A0." not in calls[0]  # 库里还没有可作父事件的
    rows = {r.event_id: r for r in db.scalars(select(NewsEvent))}
    assert rows[e2].parent_id == e1 and rows[e3].parent_id is None
    assert rows[e1].score == 4  # 父事件抬到整串最高分

    # 列表只出父事件，后续挂在下面；父事件按 4 分进要闻
    (ev,) = news.list_events(db)
    assert ev["id"] == e1 and [f["id"] for f in ev["followups"]] == [e2]
    assert ev["last_at"] == ev["followups"][0]["pub_at"]
    assert [x["id"] for x in news.list_events(db, view="all")] == [e3, e1]
    # 搜后续里的词也能找到父事件
    assert [x["id"] for x in news.list_events(db, view="all", q="嵌入式通胀", now=T0)] == [e1]
    # 叙事标题不重复算后续
    assert len(news.recent_headlines(db, now=T0 + timedelta(hours=1))) == 2


def test_classify_threads_to_anchor_from_earlier_round(db, monkeypatch):
    e1, e2, e3 = _thread_seed(db)
    db.add(NewsEvent(event_id=e1, category="央行货币", score=4, sectors=[]))
    db.commit()
    # 本轮只有 e2、e3 待标；A0 = e1
    calls = _fake_llm(monkeypatch, {"items": [
        {"i": 0, "c": "央行货币", "s": 3, "p": "A0"},
        {"i": 1, "c": "其他", "s": 2, "p": "A7"},  # 不存在的锚点：忽略
    ]})
    news_classify.classify_pending(db, T0 + timedelta(minutes=4))
    assert "A0. 美联储理事库克：高通胀" in calls[0]
    rows = {r.event_id: r for r in db.scalars(select(NewsEvent))}
    assert rows[e2].parent_id == e1 and rows[e3].parent_id is None
    assert rows[e1].score == 4  # 后续分数低，不改父事件


def test_thread_sources_make_multi_source_speech_featured(db, monkeypatch):
    t = T0
    lines = ["美联储理事库克：高通胀并未给美国央行留出耐心等待的空间。",
             "美联储理事库克：嵌入式通胀将要求美联储采取更激进的应对措施。",
             "美联储理事库克表示，当前金融条件过于宽松，物价在广泛品类中快速上涨。"]
    feeds = {s: Feed(_item(s, "1", lines[k], t + timedelta(seconds=k))) for k, s in enumerate(["a", "b", "c"])}
    Collector(feeds).run(db, t + timedelta(minutes=1))
    e1, e2, e3 = sorted(db.scalars(select(NewsItem.event_id).distinct()))
    assert len({e1, e2, e3}) == 3  # 字面不够像，采集时没合并
    _fake_llm(monkeypatch, {"items": [
        {"i": 0, "c": "央行货币", "s": 3}, {"i": 1, "c": "央行货币", "s": 3, "p": 0},
        {"i": 2, "c": "央行货币", "s": 3, "p": 1},  # 指向后续 → 解析到根
    ]})
    news_classify.classify_pending(db, t + timedelta(minutes=2))
    rows = {r.event_id: r for r in db.scalars(select(NewsEvent))}
    assert rows[e3].parent_id == e1 and rows[e1].thread_sources == 3
    (ev,) = news.list_events(db)  # 每条 3 分、各一家，整串三家 → 要闻
    assert ev["id"] == e1 and ev["featured"] and len(ev["followups"]) == 2


def test_classify_anchor_excludes_low_score_and_old_events(db, monkeypatch):
    e1, e2, _ = _thread_seed(db)
    db.add(NewsEvent(event_id=e1, category="央行货币", score=2, sectors=[]))  # 2 分不当父事件
    db.commit()
    calls = _fake_llm(monkeypatch, {"items": []})
    news_classify.classify_pending(db, T0 + timedelta(minutes=4))
    assert "A0." not in calls[0]


def test_split_title_strips_boilerplate_in_title():
    assert news.split_title("财联社11月21日电，", "特朗普称利率过高。") == ("", "特朗普称利率过高。")


def test_classify_failure_writes_nothing_and_retries(db, monkeypatch):
    _seed(db)
    _fake_llm(monkeypatch, RuntimeError("timeout"))
    now = T0 + timedelta(minutes=3)
    assert news_classify.classify_pending(db, now) == 0
    assert db.scalar(select(func.count()).select_from(NewsEvent)) == 0
    _fake_llm(monkeypatch, {"items": []})
    assert news_classify.classify_pending(db, now) == 4


def test_classify_skips_events_older_than_lookback(db, monkeypatch):
    _seed(db)
    _fake_llm(monkeypatch, {"items": []})
    assert news_classify.classify_pending(db, T0 + timedelta(days=2)) == 0


def test_classify_disabled_without_llm(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(news_classify.llm, "enabled", lambda: False)
    assert news_classify.classify_pending(db, T0) == 0


def test_narrative_reads_news_item_and_falls_back(db, monkeypatch):
    from app.services import narrative

    monkeypatch.setattr(narrative, "MIN_DB_HEADLINES", 3)
    monkeypatch.setattr(news, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: T0 + timedelta(hours=1))}))
    _seed(db)
    assert len(narrative.fetch_headlines(db)) == 4

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
    assert len(c.get("/news/events").json()["events"]) == 1  # 默认只看要闻
    body = c.get("/news/events", params={"limit": 2, "view": "all"}).json()
    assert len(body["events"]) == 2 and body["next"] == body["events"][-1]["id"]
    assert c.get("/news/events", params={"before": body["next"], "view": "all"}).json()["next"] is None
    assert c.get("/news/events", params={"view": "bogus"}).status_code == 422
    assert "宏观数据" in c.get("/news/categories").json()
    srcs = c.get("/news/sources").json()
    assert [s["source"] for s in srcs] == ["cls", "jin10", "wscn", "sina", "ths"]
