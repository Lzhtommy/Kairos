"""BaoStock（证券宝）：免费、免注册的 A 股历史数据源，走自有 TCP 协议（public-api.baostock.com:10030）。

覆盖沪深全部证券（含已退市）、上市 / 退市日期、日 K、指数成分；没有北交所。
login 是模块级全局会话，只在盘后批量任务里串行调用，不能多线程并发。
单股全历史日 K 在 ECS 上要 2~15 秒，只用来补腾讯拿不到的退市股，在市股票仍走腾讯。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

# 指数代码 → BaoStock 成分查询函数名
INDEX_QUERIES = {
    "000016": "query_sz50_stocks",
    "000300": "query_hs300_stocks",
    "000905": "query_zz500_stocks",
}


@contextmanager
def session() -> Iterator[object]:
    import baostock as bs  # requirements-data 里的可选依赖

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    try:
        yield bs
    finally:
        bs.logout()


def _rows(rs) -> list[dict[str, str]]:  # noqa: ANN001
    if rs.error_code != "0":
        raise RuntimeError(f"baostock query failed: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(dict(zip(rs.fields, rs.get_row_data())))
    return rows


def fetch_security_basics() -> list[dict[str, str]]:
    """全部证券的基本资料，字段：code（sh.600519）、code_name、ipoDate、outDate、type、status。

    全量约 9000 条，ECS 上实测约 40 秒。
    """
    with session() as bs:
        return _rows(bs.query_stock_basic())


def fetch_daily_qfq(bs, symbol: str, start: str = "1990-01-01", end: str = "2100-01-01") -> list[dict[str, str]]:  # noqa: ANN001
    """前复权日 K（symbol 形如 sh.600001），停牌日（tradestatus=0）也会返回一行。"""
    return _rows(bs.query_history_k_data_plus(
        symbol, "date,open,high,low,close,volume,tradestatus",
        start_date=start, end_date=end, frequency="d", adjustflag="2",
    ))


def fetch_daily_basic(bs, symbol: str, start: str = "1990-01-01", end: str = "2100-01-01") -> list[dict[str, str]]:  # noqa: ANN001
    """不复权日线附带的每日指标：ST 标记、停牌、换手率、成交额、PE(TTM) / PB(MRQ) / PS(TTM) / PCF(TTM)。

    停牌日也返回一行（tradestatus=0，成交额和换手为空）；volume 单位股，amount 单位元。
    """
    return _rows(bs.query_history_k_data_plus(
        symbol, "date,close,preclose,volume,amount,turn,tradestatus,isST,peTTM,pbMRQ,psTTM,pcfNcfTTM",
        start_date=start, end_date=end, frequency="d", adjustflag="3",
    ))


def fetch_index_members(bs, index_code: str, day: str) -> list[dict[str, str]]:  # noqa: ANN001
    """day 当天生效的指数成分，字段 updateDate（成分调整日）、code、code_name。"""
    return _rows(getattr(bs, INDEX_QUERIES[index_code])(date=day))
