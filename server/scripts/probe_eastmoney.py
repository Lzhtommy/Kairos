"""探测东财 push2delay/push2his 集群与申万数据源的可用性（只读，无副作用）。

背景：provider auto 链在生产 ECS 上从未真正试过东财集群；行业轮动模块
（sector provider）依赖 push2his 板块 K 线或申万 akshare 口径，上线前先跑
本脚本确认哪条路通（research PLAN Step 5.1）。

用法（生产上经 Maintenance workflow）：
    docker compose exec -T api python -m scripts.probe_eastmoney
本地：cd server && .venv/bin/python -m scripts.probe_eastmoney
"""

from __future__ import annotations

import sys
import time

import httpx

_DELAY = "https://push2delay.eastmoney.com"
_HIS = "https://push2his.eastmoney.com"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}


def _probe(name: str, fn) -> bool:
    t0 = time.monotonic()
    try:
        detail = fn()
        ms = (time.monotonic() - t0) * 1000
        print(f"  [OK]   {name:<28s} {ms:6.0f}ms  {detail}")
        return True
    except Exception as exc:  # noqa: BLE001 — 探测脚本要报告而不是崩溃
        ms = (time.monotonic() - t0) * 1000
        print(f"  [FAIL] {name:<28s} {ms:6.0f}ms  {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    client = httpx.Client(timeout=15, headers=_HEADERS, trust_env=False)

    def get(url: str, params: dict) -> dict:
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    def stock_clist():
        data = get(f"{_DELAY}/api/qt/clist/get", {
            "pn": 1, "pz": 5, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f12", "fs": "m:1+t:2", "fields": "f12,f14",
        })
        rows = (data.get("data") or {}).get("diff") or []
        if not rows:
            raise RuntimeError("empty diff")
        return f"{len(rows)} rows, first={rows[0].get('f12')} {rows[0].get('f14')}"

    def index_ulist():
        data = get(f"{_DELAY}/api/qt/ulist.np/get", {
            "fltt": 2, "secids": "1.000001,0.399001", "fields": "f2,f12,f14",
        })
        rows = (data.get("data") or {}).get("diff") or []
        if not rows:
            raise RuntimeError("empty diff")
        return "、".join(f"{r.get('f14')}={r.get('f2')}" for r in rows)

    def stock_kline():
        data = get(f"{_HIS}/api/qt/stock/kline/get", {
            "secid": "1.600519", "klt": 101, "fqt": 1, "end": "20500101", "lmt": 5,
            "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56",
        })
        rows = (data.get("data") or {}).get("klines") or []
        if not rows:
            raise RuntimeError("empty klines")
        return f"600519 last bar: {rows[-1].split(',')[0]}"

    board_code: list[str] = []

    def board_clist():
        data = get(f"{_DELAY}/api/qt/clist/get", {
            "pn": 1, "pz": 200, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f12", "fs": "m:90+t:2+f:!50", "fields": "f12,f14",
        })
        rows = (data.get("data") or {}).get("diff") or []
        if not rows:
            raise RuntimeError("empty diff")
        board_code.append(str(rows[0].get("f12")))
        return f"{len(rows)} boards, first={rows[0].get('f12')} {rows[0].get('f14')}"

    def board_kline():
        code = board_code[0] if board_code else "BK0475"
        data = get(f"{_HIS}/api/qt/stock/kline/get", {
            "secid": f"90.{code}", "klt": 101, "fqt": 1, "end": "20500101", "lmt": 5,
            "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56,f57",
        })
        rows = (data.get("data") or {}).get("klines") or []
        if not rows:
            raise RuntimeError("empty klines")
        return f"{code} last bar: {rows[-1].split(',')[0]}"

    def sw_akshare():
        import akshare as ak  # noqa: PLC0415 — 可选依赖

        df = ak.index_hist_sw(symbol="801010", period="day")
        if df.empty:
            raise RuntimeError("empty dataframe")
        return f"801010 农林牧渔 {len(df)} bars, last={df['日期'].iloc[-1]}"

    def cls_headlines():
        import akshare as ak  # noqa: PLC0415

        df = ak.stock_info_global_cls()
        if df.empty:
            raise RuntimeError("empty dataframe")
        return f"财联社电报 {len(df)} 条"

    print("东财集群（个股/指数，provider auto 链兜底）：")
    ok_em = _probe("push2delay clist (股票)", stock_clist)
    _probe("push2delay ulist (指数)", index_ulist)
    _probe("push2his kline (个股)", stock_kline)

    print("\n行业板块（sector provider 两条路）：")
    ok_board = _probe("push2delay clist (板块)", board_clist)
    ok_board = _probe("push2his kline (板块)", board_kline) and ok_board
    ok_sw = _probe("akshare index_hist_sw (申万)", sw_akshare)

    print("\n叙事管道新闻源（PLAN Step 4）：")
    _probe("akshare 财联社电报", cls_headlines)

    print("\n结论：")
    if ok_sw:
        print("  - 申万研究口径可用，sector_source 建议保持 auto（默认优先 sw）")
    if ok_board and not ok_sw:
        print("  - 申万不可用但东财板块可用，sector provider 将自动回落 eastmoney 口径")
    if not ok_board and not ok_sw:
        print("  - 两条路都不通，行业轮动模块暂无法在本机上线")
    if ok_em:
        print("  - 东财个股集群可用，provider auto 链的 eastmoney 兜底成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())
