"""策略 DSL 的统一执行入口。

两段式：标量因子（PE/ROE/行业…）先在全市场快照上筛一遍——这一步很快；
K 线技术条件只对入围的小集合取日 K 计算，避免为全市场加载百万行 K 线。
strategies / backtest 都应该走这里，而不是直接调 dsl.execute。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services import technical
from app.services.dsl import execute, validate_dsl
from app.services.market import factor_rows


def run_dsl(db: Session, dsl: dict[str, Any]) -> list[dict[str, Any]]:
    """返回通过全部条件（标量 + 技术形态）的因子行。"""
    dsl = validate_dsl(dsl)
    passing = execute(dsl, factor_rows(db))
    tech = dsl.get("technical") or []
    if not tech or not passing:
        return passing
    series = technical.series_by_code(
        db, [r["code"] for r in passing], technical.bars_needed(tech)
    )
    out = []
    for r in passing:
        bars = series.get(r["code"]) or technical.Bars(close=[])
        if technical.passes(tech, bars):
            out.append(r)
    return out
