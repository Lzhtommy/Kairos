"""行业轮动模块：指标公式、倾斜符号自适应、日更任务幂等、API 形状、叙事落库。"""

from __future__ import annotations

import math
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.models.sector import SectorBar, SectorInfo, SectorMetric, SectorNarrative, SectorTilt
from app.services.sector_metrics import build_panel, calc_rrg, crowding_rows, tilt_rows
from tests.conftest import trading_days


def _bars(days, closes, amounts=None):
    amounts = amounts or [100.0] * len(days)
    return [(d, c, a) for d, c, a in zip(days, closes, amounts)]


def _geo_closes(days_per_month: list[int], monthly_returns: list[float], start=100.0):
    """按月度目标收益生成逐日收盘（月内等比）。"""
    closes: list[float] = []
    px = start
    for n, r in zip(days_per_month, monthly_returns):
        daily = (1 + r) ** (1 / n) - 1
        for _ in range(n):
            px *= 1 + daily
            closes.append(px)
    return closes


# ---------------------------------------------------------------- 拥挤度
def test_crowding_flags_hot_sector():
    days = trading_days("2024-01-01", 470)
    quiet = _bars(days, [100.0] * 470, [100.0] * 470)
    hot_amounts = [100.0] * 450 + [500.0] * 20  # 最后一个月放量
    hot = _bars(days, [100.0] * 470, hot_amounts)
    panel = build_panel({"A": quiet, "B": hot})
    rows = [r for r in crowding_rows(panel) if r["ts"] == days[-1]]
    by_code = {r["code"]: r for r in rows}
    assert by_code["B"]["crowd"] is not None
    assert by_code["B"]["heat"] > 0.95           # 放量板块换手热度打到分位顶部
    assert by_code["B"]["crowd"] > by_code["A"]["crowd"]


def test_crowding_since_filter_limits_dates():
    days = trading_days("2024-01-01", 300)
    panel = build_panel({"A": _bars(days, [100.0] * 300)})
    rows = crowding_rows(panel, since=days[-5])
    assert {r["ts"] for r in rows} == set(days[-5:])


# ---------------------------------------------------------------- RRG
def test_rrg_strong_sector_upper_half():
    days = trading_days("2024-01-01", 220)
    strong = [100.0 * 1.002**i for i in range(220)]
    weak = [100.0] * 220
    panel = build_panel({"S": _bars(days, strong), "W": _bars(days, weak)})
    rrg = calc_rrg(panel)
    by_code = {s["code"]: s for s in rrg["sectors"]}
    assert set(by_code) == {"S", "W"}
    assert len(by_code["S"]["trail"]) == 6
    # 持续跑赢等权基准 → RS-Ratio 在 100 上方，跑输的在下方
    assert by_code["S"]["trail"][-1][0] > 100
    assert by_code["W"]["trail"][-1][0] < 100
    assert by_code["S"]["rel4w"] > by_code["W"]["rel4w"]


def test_rrg_skips_short_history():
    days = trading_days("2024-01-01", 220)
    panel = build_panel({
        "S": _bars(days, [100.0 * 1.001**i for i in range(220)]),
        "NEW": _bars(days[-30:], [100.0] * 30),  # 上市不足 26 周
    })
    codes = {s["code"] for s in calc_rrg(panel)["sectors"]}
    assert "NEW" not in codes


# ---------------------------------------------------------------- 倾斜
def _monthly_panel_bars(n_sectors: int, monthly: dict[str, list[float]]):
    """monthly: code -> 每月收益列表；每月 21 个交易日。"""
    n_months = len(next(iter(monthly.values())))
    days = trading_days("2020-01-01", n_months * 21)
    out = {}
    for code, rets in monthly.items():
        closes = _geo_closes([21] * n_months, rets)
        out[code] = _bars(days, closes)
    return build_panel(out)


def test_tilt_momentum_panel_top_suggestion():
    # 12 个板块、恒定收益差 → rev1 IC 恒正（延续），高收益板块应获高配
    monthly = {f"S{i:02d}": [0.001 * i] * 30 for i in range(12)}
    panel = _monthly_panel_bars(12, monthly)
    rows = tilt_rows(panel)
    months = sorted({r["month"] for r in rows})
    last = [r for r in rows if r["month"] == months[-1]]
    by_code = {r["code"]: r for r in last}
    assert by_code["S11"]["suggestion"] == "高配"
    assert by_code["S00"]["suggestion"] == "低配"
    assert by_code["S11"]["rank"] == 1
    # 最后一个（不完整）月不出建议
    assert months[-1] < datetime(2020 + (30 * 21 // 250), 12, 1)


def test_tilt_sign_adapts_to_reversal_panel():
    # 奇偶月符号翻转 → rev1 IC 恒为 -1，符号自适应后高 rev1 板块应得低分
    monthly = {
        f"S{i:02d}": [(0.002 * i if m % 2 == 0 else -0.002 * i) for m in range(40)]
        for i in range(12)
    }
    panel = _monthly_panel_bars(12, monthly)
    rows = tilt_rows(panel)
    months = sorted({r["month"] for r in rows})
    last = [r for r in rows if r["month"] == months[-1]]
    by_code = {r["code"]: r for r in last}
    top = max(last, key=lambda r: r["score"])
    bottom = min(last, key=lambda r: r["score"])
    # 翻转面板上 rev1_z 与 score 反号：z 最高的板块分数不应最高
    assert by_code[top["code"]]["rev1_z"] < by_code[bottom["code"]]["rev1_z"]


# ---------------------------------------------------------------- 日更任务
class FakeSource:
    name = "fake"

    def __init__(self, panel_bars):
        self._bars = panel_bars

    def list_sectors(self):
        return [(code, f"板块{code}") for code in self._bars]

    def get_bars(self, code, limit=3000):
        from app.providers.sector_provider import SectorBarData

        return [
            SectorBarData(ts=d, open=c, high=c, low=c, close=c, volume=100, amount=a)
            for d, c, a in self._bars[code][-limit:]
        ]


@pytest.fixture()
def _sector_env(db, monkeypatch):
    from app.jobs import sector as sector_job

    days = trading_days("2023-01-01", 460)
    bars = {
        f"S{i:02d}": _bars(days, [100.0 * (1 + 0.0001 * i) ** k for k in range(460)])
        for i in range(12)
    }
    monkeypatch.setattr(sector_job, "SessionLocal", lambda: db)
    monkeypatch.setattr(sector_job, "in_trading_session", lambda: False)
    monkeypatch.setattr(sector_job, "get_sector_source", lambda preferred: FakeSource(bars))
    monkeypatch.setattr(sector_job, "throttle", lambda: None)
    return sector_job


def test_update_sectors_idempotent(db, _sector_env):
    _sector_env.update_sectors()
    n_bars = db.query(SectorBar).count()
    n_metrics = db.query(SectorMetric).count()
    n_tilts = db.query(SectorTilt).count()
    assert n_bars == 12 * 460
    assert n_metrics > 0
    assert n_tilts > 0
    assert db.query(SectorInfo).first().source == "fake"

    _sector_env.update_sectors()  # 再跑一遍不重复写
    assert db.query(SectorBar).count() == n_bars
    assert db.query(SectorMetric).count() == n_metrics
    assert db.query(SectorTilt).count() == n_tilts


# ---------------------------------------------------------------- 叙事
def test_update_narrative_writes_and_skips(db, monkeypatch):
    from app.core.config import settings
    from app.jobs import narrative as narrative_job

    db.add(SectorInfo(code="801080", name="电子", source="sw"))
    db.add(SectorInfo(code="801780", name="银行", source="sw"))
    db.commit()

    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    monkeypatch.setattr(settings, "ai_provider", "deepseek")
    monkeypatch.setattr(narrative_job, "SessionLocal", lambda: db)
    monkeypatch.setattr(narrative_job, "fetch_headlines", lambda: ["半导体大基金三期落地", "无关新闻"])

    def fake_score(headlines, names):
        assert "电子" in names
        return {
            "market": {"direction": 0.3, "strength": 0.4, "summary": "整体偏暖"},
            "sectors": [{
                "name": "电子", "direction": 0.8, "strength": 0.7,
                "summary": "大基金三期利好半导体", "evidence": [headlines[0]],
            }],
        }

    monkeypatch.setattr(narrative_job, "score_narrative", fake_score)
    narrative_job.update_narrative()

    rows = db.query(SectorNarrative).all()
    assert {r.code for r in rows} == {"_market", "801080"}
    elec = next(r for r in rows if r.code == "801080")
    assert elec.direction == 0.8
    assert elec.evidence == ["半导体大基金三期落地"]

    narrative_job.update_narrative()  # 当日已有记录 → 幂等
    assert db.query(SectorNarrative).count() == len(rows)


def test_score_narrative_parses_and_clips(monkeypatch):
    import httpx

    from app.core.config import settings
    from app.services import narrative as svc

    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    monkeypatch.setattr(settings, "ai_provider", "auto")

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            import json

            content = json.dumps({
                "market": {"direction": 5, "strength": -1, "summary": "s"},
                "sectors": [
                    {"name": "电子", "direction": 0.5, "strength": 0.6,
                     "summary": "x", "evidence": [0, 99]},
                    {"name": "不存在的板块", "direction": 1, "strength": 1,
                     "summary": "y", "evidence": []},
                    {"name": "银行", "direction": 0.1, "strength": 0.1,
                     "summary": "无直接叙事", "evidence": []},
                ],
            })
            return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    out = svc.score_narrative(["标题A", "标题B"], ["电子", "银行"])
    assert out["market"]["direction"] == 1    # 越界值被裁剪
    assert out["market"]["strength"] == 0
    assert len(out["sectors"]) == 1           # 名单外板块、无证据弱行都被丢弃
    assert out["sectors"][0]["evidence"] == ["标题A"]  # 非法编号被丢弃


# ---------------------------------------------------------------- API
def _api_client(db) -> TestClient:
    from app.api.sector import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_overview_empty(db):
    body = _api_client(db).get("/sector/overview").json()
    assert body["source"] is None
    assert body["sectors"] == []


def test_overview_shape(db):
    days = trading_days("2024-01-01", 220)
    for i, code in enumerate(["801080", "801780"]):
        db.add(SectorInfo(code=code, name=f"板块{i}", source="sw"))
        for k, d in enumerate(days):
            px = 100.0 * (1 + 0.0005 * i) ** k
            db.add(SectorBar(code=code, ts=d, open=px, high=px, low=px,
                             close=px, volume=100, amount=50.0))
    db.add(SectorMetric(code="801080", ts=days[-1], crowd=0.9,
                        crowd_share=0.8, crowd_heat=0.95, crowd_bias=0.85))
    db.add(SectorTilt(code="801080", month=datetime(2024, 9, 1), rev1_z=1.2,
                      season_z=None, resmom_z=None, score=1.2, rank=1, suggestion="高配"))
    db.add(SectorNarrative(date=days[-1], code="801080", direction=0.5,
                           strength=0.6, summary="利好", evidence=["标题"], model="deepseek-chat"))
    db.add(SectorNarrative(date=days[-1], code="_market", direction=0.1,
                           strength=0.2, summary="平静", evidence=[], model="deepseek-chat"))
    db.commit()

    body = _api_client(db).get("/sector/overview").json()
    assert body["source"] == "sw"
    assert body["tilt_month"] == "2024-09"
    assert body["market_narrative"]["summary"] == "平静"
    by_code = {s["code"]: s for s in body["sectors"]}
    assert by_code["801080"]["crowd"]["crowd"] == 0.9
    assert by_code["801080"]["tilt"]["suggestion"] == "高配"
    assert by_code["801080"]["narrative"]["summary"] == "利好"
    assert by_code["801080"]["rrg"] is not None
    assert math.isclose(by_code["801080"]["close"], 100.0)

    hist = _api_client(db).get("/sector/narrative").json()
    assert len(hist) == 1
    assert hist[0]["market"]["summary"] == "平静"
    assert hist[0]["items"][0]["code"] == "801080"
