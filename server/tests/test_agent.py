"""AI 操盘手：费率、下单校验、按日 K 撮合结算、除权除息、工具调用循环、单次运行落库。不碰网络。"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import httpx
import pytest

from app.core.config import settings
from app.models.agent import AgentEquity, AgentMemory, AgentOrder, AgentPosition, AgentSession
from app.models.warehouse import Dividend
from app.services import agent as agent_svc
from app.services import agent_broker as broker
from app.services import agent_tools as tools
from app.services import llm
from tests.conftest import add_stock, trading_days

DAYS = trading_days("2026-03-02", 5)
PRE_OPEN = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)  # 早于所有交易日 09:30


def _acct(db, cash=100_000.0):
    acct = agent_svc.create_account(db, "测试", cash, None)
    acct.fees = {**broker.DEFAULT_FEES, "slippageBps": 0.0}
    db.commit()
    return acct


def _order(db, acct, code, side, shares, limit=None, created=PRE_OPEN, mem=None):
    o = AgentOrder(account_id=acct.id, code=code, name=code, side=side, shares=shares, limit_price=limit,
                   reason="t", memory_ids=mem or [], created_at=created)
    db.add(o)
    db.commit()
    return o


# ---------------------------------------------------------------- 费率与手数


def test_trade_fee_min_commission_stamp_and_transfer():
    f = broker.DEFAULT_FEES
    assert broker.trade_fee(f, "buy", "600000", 10_000) == 5.1  # 最低佣金 5 + 过户费 0.1
    assert broker.trade_fee(f, "sell", "600000", 100_000) == 76.0  # 佣金 25 + 印花税 50 + 过户费 1
    assert broker.trade_fee(f, "sell", "830000", 100_000) == 75.0  # 北交所无过户费


def test_lot_rules():
    assert broker.lot_rule("600000") == (100, 100)
    assert broker.lot_rule("688001") == (200, 1)
    assert broker.lot_rule("920001") == (100, 1)


def test_place_order_validation(db):
    add_stock(db, "600000", DAYS, [10.0] * 5)
    add_stock(db, "688001", DAYS, [10.0] * 5)
    acct = _acct(db)
    with pytest.raises(ValueError, match="100 股整数倍|递增"):
        broker.place_order(db, acct, code="600000", side="buy", shares=150, limit_price=None, reason="")
    with pytest.raises(ValueError, match="≥200"):
        broker.place_order(db, acct, code="688001", side="buy", shares=100, limit_price=None, reason="")
    broker.place_order(db, acct, code="688001", side="buy", shares=201, limit_price=None, reason="")
    with pytest.raises(ValueError, match="可用资金不足"):
        broker.place_order(db, acct, code="600000", side="buy", shares=10_000, limit_price=None, reason="")
    with pytest.raises(ValueError, match="可卖 0 股"):
        broker.place_order(db, acct, code="600000", side="sell", shares=100, limit_price=None, reason="")
    with pytest.raises(ValueError, match="找不到"):
        broker.place_order(db, acct, code="999999", side="buy", shares=100, limit_price=None, reason="")


def test_pending_buys_reserve_cash(db):
    add_stock(db, "600000", DAYS, [10.0] * 5)
    acct = _acct(db, cash=20_000)
    broker.place_order(db, acct, code="600000", side="buy", shares=1500, limit_price=None, reason="")
    with pytest.raises(ValueError, match="可用资金不足"):
        broker.place_order(db, acct, code="600000", side="buy", shares=500, limit_price=None, reason="")


# ---------------------------------------------------------------- 撮合结算


def test_round_trip_settlement(db):
    add_stock(db, "600000", DAYS, closes=[10.0, 10.5, 11.0, 11.0, 11.0], opens=[10.0, 10.2, 10.8, 11.0, 11.0],
              volumes=[1_000_000] * 5)
    acct = _acct(db)
    acct.last_settled = DAYS[0]
    _order(db, acct, "600000", "buy", 1000)
    broker.settle_day(db, acct, DAYS[1])
    pos = db.get(AgentPosition, (acct.id, "600000"))
    buy_fee = broker.trade_fee(broker.DEFAULT_FEES, "buy", "600000", 10_200)
    assert pos.shares == 1000 and pos.cost == pytest.approx(10_200 + buy_fee)
    assert acct.cash == pytest.approx(100_000 - 10_200 - buy_fee)
    eq = db.get(AgentEquity, (acct.id, DAYS[1]))
    assert eq.market_value == 10_500 and eq.total == pytest.approx(acct.cash + 10_500)

    # 次日开盘卖出：印花税 + 实现盈亏扣费
    _order(db, acct, "600000", "sell", 1000, created=datetime(2026, 3, 3, 8, 0, tzinfo=timezone.utc))
    broker.settle_day(db, acct, DAYS[2])
    sell = db.query(AgentOrder).filter_by(side="sell").one()
    sell_fee = broker.trade_fee(broker.DEFAULT_FEES, "sell", "600000", 10_800)
    assert sell.status == "filled" and sell.fill_price == 10.8
    assert sell.realized_pnl == pytest.approx(10_800 - sell_fee - 10_200 - buy_fee, abs=0.01)
    assert db.get(AgentPosition, (acct.id, "600000")) is None
    assert acct.cash == pytest.approx(100_000 - buy_fee - sell_fee + 600, abs=0.01)
    # 幂等：重复结算同一天不重复撮合
    broker.settle_day(db, acct, DAYS[2])
    assert db.query(AgentEquity).count() == 2


def test_orders_after_open_wait_for_next_day(db):
    add_stock(db, "600000", DAYS, [10.0] * 5)
    acct = _acct(db)
    o = _order(db, acct, "600000", "buy", 100, created=datetime(2026, 3, 3, 3, 0, tzinfo=timezone.utc))  # 3 日 11:00
    broker.settle_day(db, acct, DAYS[1])  # 3 月 3 日
    assert o.status == "pending"
    broker.settle_day(db, acct, DAYS[2])
    assert o.status == "filled" and o.trade_date == DAYS[2]


def test_one_word_limit_up_cannot_buy_and_limit_not_reached(db):
    add_stock(db, "600000", DAYS, closes=[10.0, 11.0, 11.0, 11.0, 11.0],
              opens=[10.0, 11.0, 11.0, 11.0, 11.0], highs=[10.0, 11.0, 11.2, 11.0, 11.0],
              lows=[10.0, 11.0, 10.9, 11.0, 11.0])
    acct = _acct(db)
    a = _order(db, acct, "600000", "buy", 100)
    b = _order(db, acct, "600000", "buy", 100, limit=10.5)
    broker.settle_day(db, acct, DAYS[1])
    assert a.status == "expired" and "一字涨停" in a.note
    assert b.status == "expired"
    c = _order(db, acct, "600000", "buy", 100, limit=10.95, created=datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc))
    broker.settle_day(db, acct, DAYS[2])
    assert c.status == "filled" and c.fill_price == 10.95  # 低开不到限价时按 min(开盘, 限价)


def test_buy_shrinks_to_affordable_lots(db):
    add_stock(db, "600000", DAYS, closes=[10.0, 12.0, 12.0, 12.0, 12.0], opens=[10.0, 12.0, 12.0, 12.0, 12.0],
              volumes=[10_000_000] * 5)
    acct = _acct(db, cash=10_000)
    o = _order(db, acct, "600000", "buy", 900)  # 下单时按 10 元估，开盘跳到 12 元
    broker.settle_day(db, acct, DAYS[1])
    assert o.status == "filled" and o.filled_shares == 800 and "部分成交" in o.note
    assert acct.cash >= 0


def test_volume_cap(db):
    add_stock(db, "600000", DAYS, [10.0] * 5, volumes=[10_000] * 5)
    acct = _acct(db)
    o = _order(db, acct, "600000", "buy", 1000)
    broker.settle_day(db, acct, DAYS[1])
    assert o.filled_shares == 500  # 不超过当日成交量 5%


def test_t_plus_one_sell_same_day_as_buy(db):
    add_stock(db, "600000", DAYS, [10.0] * 5)
    acct = _acct(db)
    buy = _order(db, acct, "600000", "buy", 100)
    sell = _order(db, acct, "600000", "sell", 100)
    broker.settle_day(db, acct, DAYS[1])
    assert buy.status == "filled"
    assert sell.status == "expired" and "无可卖持仓" in sell.note


def test_cash_dividend_with_tax(db):
    add_stock(db, "600000", DAYS, [10.0] * 5)
    acct = _acct(db)
    db.add(AgentPosition(account_id=acct.id, code="600000", name="x", shares=1000, cost=10_000, last_price=10,
                         opened_at=DAYS[0]))
    db.add(Dividend(code="600000", report_date=DAYS[0].date(), ex_date=DAYS[2].date(), cash_per10=5.0,
                    progress="实施分配", plan="10派5"))
    db.commit()
    cash0 = acct.cash
    broker.settle_day(db, acct, DAYS[2])
    assert acct.cash == pytest.approx(cash0 + 1000 * 0.5 * 0.8)  # 持有 ≤1 月，红利税 20%


def test_settle_due_skips_intraday_bar(db, monkeypatch):
    add_stock(db, "600519", DAYS, [100.0] * 5)
    acct = _acct(db)
    acct.created_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    db.commit()
    monkeypatch.setattr(broker, "cst_now", lambda: datetime(2026, 3, 6, 11, 0))
    monkeypatch.setattr(broker, "cst_today", lambda: datetime(2026, 3, 6))
    done = broker.settle_due(db, acct)
    assert done == DAYS[:4]  # 3 月 6 日盘中不结算
    assert acct.last_settled == DAYS[3]


# ---------------------------------------------------------------- 工具


def _ctx(db, acct, can_trade=True, playbook=False):
    s = AgentSession(account_id=acct.id, kind="manual", trade_date=DAYS[0])
    db.add(s)
    db.commit()
    return tools.Ctx(db=db, account=acct, session=s, can_trade=can_trade, can_edit_playbook=playbook)


def test_tool_gating_and_errors(db, monkeypatch):
    add_stock(db, "600000", DAYS, [10.0] * 5)
    acct = _acct(db)
    monkeypatch.setattr(settings, "tavily_api_key", "")
    ro = _ctx(db, acct, can_trade=False, playbook=True)
    names = {s["name"] for s in tools.specs(ro)}
    assert "place_order" not in names and "web_search" not in names and "update_playbook" in names
    assert tools.execute(ro, "place_order", {}).startswith("错误：没有工具")
    rw = _ctx(db, acct)
    assert "错误" in tools.execute(rw, "place_order", {"code": "600000", "side": "buy", "shares": 50, "reason": "x"})
    out = tools.execute(rw, "place_order", {"code": "600000", "side": "buy", "shares": 100, "reason": "x",
                                            "memory_ids": [3]})
    assert '"ok":true' in out
    assert db.query(AgentOrder).one().memory_ids == [3]
    assert '"matched":1' in tools.execute(rw, "screen_stocks", {"filters": {"pe": {"max": 20}}})
    assert "600000" in tools.execute(rw, "get_stock", {"code": "600000"})


def test_update_playbook_versions(db):
    acct = _acct(db)
    ctx = _ctx(db, acct, can_trade=False, playbook=True)
    tools.execute(ctx, "update_playbook", {"content": "新手册" * 30, "rationale": "样本外回撤过大"})
    assert acct.playbook_version == 1 and acct.playbook.startswith("新手册")


# ---------------------------------------------------------------- 运行与反思


def test_run_session_records_trace(db, monkeypatch):
    add_stock(db, "600000", DAYS, [10.0] * 5)
    acct = _acct(db)
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "model_name", lambda: "fake")

    def fake_run(system, user, specs, call, **kw):
        assert "交易手册" in system and "盘前" in user
        call("save_memory", {"content": "放量突破后次日低开不追", "kind": "rule", "tags": ["追高"]})
        call("place_order", {"code": "600000", "side": "buy", "shares": 100, "reason": "测试"})
        return "## 今日计划\n买入 600000", 1234

    monkeypatch.setattr(llm, "run_tools", fake_run)
    s = agent_svc.run_session(db, acct, "premarket")
    assert s.status == "done" and s.tokens == 1234 and len(s.steps) == 2
    assert db.query(AgentMemory).one().session_id == s.id
    assert db.query(AgentOrder).one().session_id == s.id


def test_run_session_failure_is_recorded(db, monkeypatch):
    acct = _acct(db)
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "model_name", lambda: "fake")

    def boom(*a, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(llm, "run_tools", boom)
    s = agent_svc.run_session(db, acct, "postclose")
    assert s.status == "failed" and "ConnectError" in s.error


def test_review_stats_attributes_memories(db):
    add_stock(db, "600000", DAYS, closes=[10.0, 10.0, 11.0, 11.0, 11.0], volumes=[1_000_000] * 5)
    acct = _acct(db)
    _order(db, acct, "600000", "buy", 1000, mem=[7])
    broker.settle_day(db, acct, DAYS[1])
    _order(db, acct, "600000", "sell", 1000, created=datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc), mem=[7])
    broker.settle_day(db, acct, DAYS[2])
    stats = agent_svc.review_stats(db, acct)
    assert stats["memoryOutcomes"][7]["orders"] == 2
    assert stats["memoryOutcomes"][7]["wins"] == 2
    assert stats["closedTrades"] == 1 and stats["winRate"] == 100.0


# ---------------------------------------------------------------- LLM 工具循环


class Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _script(monkeypatch, bodies):
    calls: list[dict] = []

    def fake_post(url, headers, json, timeout):  # noqa: A002
        calls.append({"url": url, "json": copy.deepcopy(json)})
        return Resp(bodies[len(calls) - 1])

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


TOOLS = [{"name": "echo", "description": "d", "parameters": {"type": "object", "properties": {}}}]


def test_run_tools_openai(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "deepseek")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    calls = _script(monkeypatch, [
        {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"x": 1}'}}]}}],
         "usage": {"total_tokens": 10}},
        {"choices": [{"message": {"content": "完成"}}], "usage": {"total_tokens": 5}},
    ])
    seen = []
    text, tokens = llm.run_tools("sys", "go", TOOLS, lambda n, a: seen.append((n, a)) or "R")
    assert (text, tokens) == ("完成", 15) and seen == [("echo", {"x": 1})]
    second = calls[1]["json"]
    assert second["tools"][0]["function"]["name"] == "echo"
    assert second["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "R"}
    assert second["thinking"] == {"type": "disabled"}  # preset extra 照常合并


def test_run_tools_anthropic_echoes_content(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    first_content = [{"type": "thinking", "thinking": "", "signature": "sig"},
                     {"type": "tool_use", "id": "t1", "name": "echo", "input": {"q": "a"}}]
    calls = _script(monkeypatch, [
        {"content": first_content, "stop_reason": "tool_use", "usage": {"input_tokens": 3, "output_tokens": 4}},
        {"content": [{"type": "text", "text": "结论"}], "stop_reason": "end_turn",
         "usage": {"input_tokens": 1, "output_tokens": 1}},
    ])
    text, tokens = llm.run_tools("sys", "go", TOOLS, lambda n, a: "R")
    assert (text, tokens) == ("结论", 9)
    body = calls[1]["json"]
    assert body["system"] == "sys" and body["tools"][0]["input_schema"]["type"] == "object"
    assert body["messages"][1] == {"role": "assistant", "content": first_content}  # thinking 块原样回传
    assert body["messages"][2]["content"] == [{"type": "tool_result", "tool_use_id": "t1", "content": "R"}]


def test_run_tools_step_cap(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "deepseek")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    looping = {"choices": [{"message": {"content": "思考中", "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "echo", "arguments": "{}"}}]}}]}
    calls = _script(monkeypatch, [looping] * 10)
    text, _ = llm.run_tools("s", "u", TOOLS, lambda n, a: "R", max_steps=2)
    assert len(calls) == 3 and text == "思考中"
    assert calls[2]["json"]["messages"][-1]["content"] == llm._FINAL_NOTICE


def test_run_tools_wrap_up_keeps_tools(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "deepseek")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    looping = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "echo", "arguments": "{}"}}]}}]}
    calls = _script(monkeypatch, [looping] * 10)
    llm.run_tools("s", "u", TOOLS, lambda n, a: "R", max_steps=5, wrap_up=(2, "去下单"))
    contents = [m["content"] for m in calls[-1]["json"]["messages"] if m["role"] == "user"]
    assert contents == ["u", "去下单", llm._FINAL_NOTICE]
    assert all("tools" in c["json"] for c in calls)  # 收尾阶段工具仍可用


def test_web_search_quota(db, monkeypatch):
    acct = _acct(db)
    monkeypatch.setattr(settings, "tavily_api_key", "k")
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: Resp({"results": [{"title": "t", "url": "u"}]}))
    ctx = _ctx(db, acct)
    outs = [tools.execute(ctx, "web_search", {"query": "q"}) for _ in range(tools.WEB_SEARCH_QUOTA + 1)]
    assert '"title":"t"' in outs[0] and "已用完" in outs[-1]
