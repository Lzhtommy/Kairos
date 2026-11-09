"""腾讯源股票全集：新浪分页中途被截断时不缓存残缺名单。"""

from __future__ import annotations

from kairos_data.sources import tencent as tencent_provider
from kairos_data.sources.tencent import TencentProvider


class _Resp:
    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return self._rows


def _rows(start: int, n: int) -> list[dict]:
    return [{"symbol": f"sz{i:06d}", "code": f"{i:06d}", "name": f"S{i}"} for i in range(start, start + n)]


def _provider(monkeypatch, pages: list[list[list[dict]]]) -> TencentProvider:
    """pages[k] = 第 k 次拉全集时依次返回的分页。"""
    p = TencentProvider()
    monkeypatch.setattr(p, "_industry_map", lambda: {})
    monkeypatch.setattr(tencent_provider, "_UNIVERSE_MIN", 250)
    calls = iter(page for sweep in pages for page in sweep)
    monkeypatch.setattr(p, "_get", lambda *a, **k: _Resp(next(calls)))
    return p


def test_truncated_universe_is_not_cached(monkeypatch):
    full = [_rows(0, 100), _rows(100, 100), _rows(200, 100), _rows(300, 10)]
    p = _provider(monkeypatch, [[_rows(0, 100), _rows(100, 0)], full])
    assert len(p.get_universe()) == 100      # 第二页被限流 → 本次只能凑合
    assert len(p.get_universe()) == 310      # 没缓存残缺名单，下次重拉拿到完整的
    assert len(p.get_universe()) == 310      # 完整名单走缓存，不再请求


def test_truncated_refresh_keeps_previous_full_universe(monkeypatch):
    full = [_rows(0, 100), _rows(100, 100), _rows(200, 100), _rows(300, 10)]
    p = _provider(monkeypatch, [full, [_rows(0, 100), _rows(100, 0)]])
    assert len(p.get_universe()) == 310
    assert len(p.get_universe(refresh=True)) == 310  # 刷新被截断 → 沿用上次完整名单
