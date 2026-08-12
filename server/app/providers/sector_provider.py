"""行业板块数据源：申万一级（研究口径）与东财行业板块（生产兜底）。

与个股 provider 分开：板块数据是轮动模块专用，不参与 factory 的行情链。
两种口径不可混用（截面指标会失真），首次落库后由 sector_info.source 锁定。

- sw：申万一级 31 个（2021 版），经 akshare index_hist_sw 直连申万宏源研究。
  研究阶段全部结论基于此口径；本地网络可用，生产 ECS 可用性未验证。
- eastmoney：东财行业板块（BK 代码，约 86 个），push2delay 列板块 +
  push2his 拉 K 线，纯 httpx 无额外依赖。生产 ECS 上用 scripts.probe_eastmoney
  验证过可用性后作为主源或兜底。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

logger = logging.getLogger("kairos.sector")

# 申万一级行业（2021 版，31 个）——与 research/sector_rotation 完全一致
SW_L1: dict[str, str] = {
    "801010": "农林牧渔", "801030": "基础化工", "801040": "钢铁",
    "801050": "有色金属", "801080": "电子", "801110": "家用电器",
    "801120": "食品饮料", "801130": "纺织服饰", "801140": "轻工制造",
    "801150": "医药生物", "801160": "公用事业", "801170": "交通运输",
    "801180": "房地产", "801200": "商贸零售", "801210": "社会服务",
    "801230": "综合", "801710": "建筑材料", "801720": "建筑装饰",
    "801730": "电力设备", "801740": "国防军工", "801750": "计算机",
    "801760": "传媒", "801770": "通信", "801780": "银行",
    "801790": "非银金融", "801880": "汽车", "801890": "机械设备",
    "801950": "煤炭", "801960": "石油石化", "801970": "环保",
    "801980": "美容护理",
}

_QUOTE_HOST = "https://push2delay.eastmoney.com"
_HIS_HOST = "https://push2his.eastmoney.com"
_BOARD_FS = "m:90+t:2+f:!50"  # 东财行业板块
_THROTTLE = 0.3               # 板块逐个拉 K 线的请求间隔


@dataclass
class SectorBarData:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float  # 亿元


class EastmoneySectorSource:
    name = "eastmoney"

    def __init__(self) -> None:
        self._client = httpx.Client(
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
            trust_env=False,
        )

    def _get_json(self, url: str, params: dict) -> dict:
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = self._client.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPStatusError, httpx.TransportError, ValueError) as exc:
                last = exc
                time.sleep(1 + attempt * 2)
        raise last  # type: ignore[misc]

    def list_sectors(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        page = 1
        while True:
            data = self._get_json(
                f"{_QUOTE_HOST}/api/qt/clist/get",
                params={
                    "pn": page, "pz": 200, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                    "fid": "f12", "fs": _BOARD_FS, "fields": "f12,f14",
                },
            )
            block = data.get("data") or {}
            rows = block.get("diff") or []
            for r in rows:
                code, name = str(r.get("f12") or ""), str(r.get("f14") or "")
                if code and name:
                    out.append((code, name))
            if not rows or len(out) >= int(block.get("total") or 0):
                break
            page += 1
        return out

    def get_bars(self, code: str, limit: int = 3000) -> list[SectorBarData]:
        data = self._get_json(
            f"{_HIS_HOST}/api/qt/stock/kline/get",
            params={
                "secid": f"90.{code}", "klt": 101, "fqt": 1,
                "end": "20500101", "lmt": limit,
                "fields1": "f1,f2,f3",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
            },
        )
        rows = (data.get("data") or {}).get("klines") or []
        out: list[SectorBarData] = []
        for row in rows:  # "date,open,close,high,low,volume(手),amount(元)"
            parts = str(row).split(",")
            if len(parts) < 7:
                continue
            out.append(
                SectorBarData(
                    ts=datetime.fromisoformat(parts[0]),
                    open=float(parts[1]),
                    high=float(parts[3]),
                    low=float(parts[4]),
                    close=float(parts[2]),
                    volume=int(float(parts[5])),
                    amount=round(float(parts[6]) / 1e8, 4),
                )
            )
        return out


class SWSectorSource:
    """申万一级，经 akshare（懒加载——核心镜像不带 akshare 时此源不可用）。"""

    name = "sw"

    def list_sectors(self) -> list[tuple[str, str]]:
        return list(SW_L1.items())

    def get_bars(self, code: str, limit: int = 0) -> list[SectorBarData]:
        import akshare as ak  # noqa: PLC0415 — 可选依赖

        last: Exception | None = None
        for attempt in range(3):
            try:
                df = ak.index_hist_sw(symbol=code, period="day")
                if df.empty:
                    raise RuntimeError("empty response")
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(2**attempt)
        else:
            raise RuntimeError(f"sw {code} 拉取失败: {last}")
        df = df.sort_values("日期").drop_duplicates("日期", keep="last")
        if limit:
            df = df.tail(limit)
        out: list[SectorBarData] = []
        for _, r in df.iterrows():
            out.append(
                SectorBarData(
                    ts=datetime.fromisoformat(str(r["日期"])),
                    open=float(r.get("开盘", r["收盘"])),
                    high=float(r.get("最高", r["收盘"])),
                    low=float(r.get("最低", r["收盘"])),
                    close=float(r["收盘"]),
                    volume=int(float(r.get("成交量", 0) or 0)),
                    amount=float(r.get("成交额", 0) or 0),  # 亿元
                )
            )
        return out


def _try_sw() -> SWSectorSource | None:
    try:
        src = SWSectorSource()
        if not src.get_bars("801010", limit=5):
            return None
        logger.info("Sector source: sw (申万一级)")
        return src
    except Exception as exc:  # noqa: BLE001
        logger.info("Sector source sw unavailable: %s", exc)
        return None


def _try_eastmoney() -> EastmoneySectorSource | None:
    try:
        src = EastmoneySectorSource()
        if not src.list_sectors():
            return None
        logger.info("Sector source: eastmoney (行业板块)")
        return src
    except Exception as exc:  # noqa: BLE001
        logger.info("Sector source eastmoney unavailable: %s", exc)
        return None


def get_sector_source(preferred: str = "auto"):
    """按偏好解析板块数据源；探测失败返回 None（调用方跳过本轮更新）。

    preferred 通常是 settings.sector_source，但库里已有数据时调用方应
    传入 sector_info.source 锁定口径。
    """
    if preferred == "off":
        return None
    if preferred == "sw":
        return _try_sw()
    if preferred == "eastmoney":
        return _try_eastmoney()
    return _try_sw() or _try_eastmoney()


def throttle() -> None:
    time.sleep(_THROTTLE)
