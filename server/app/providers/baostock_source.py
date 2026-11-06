"""BaoStock（证券宝）：免费、免注册的 A 股历史数据源，走自有 TCP 协议（public-api.baostock.com:10030）。

覆盖沪深全部证券（含已退市）、上市 / 退市日期、复权因子；没有北交所。
login 是模块级全局会话，只在盘后批量任务里串行调用，不能多线程并发。
"""

from __future__ import annotations


def fetch_security_basics() -> list[dict[str, str]]:
    """全部证券的基本资料，字段：code（sh.600519）、code_name、ipoDate、outDate、type、status。

    全量约 9000 条，ECS 上实测约 40 秒。
    """
    import baostock as bs  # requirements-data 里的可选依赖

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    try:
        rs = bs.query_stock_basic()
        if rs.error_code != "0":
            raise RuntimeError(f"baostock query_stock_basic failed: {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(dict(zip(rs.fields, rs.get_row_data())))
        return rows
    finally:
        bs.logout()
