"""一次性迁移：ma_trend.lookback 语义从"K 线根数"改为"均线值个数"。

老语义下 lookback 根 K 线里只有 lookback-window+1 个均线值；按此换算可保持
既有策略行为不变：

    lookback > window 时: lookback' = lookback - window + 1
    lookback ≤ window 时: 原样保留——老语义下这是非法组合（signal_series 会
    越界崩溃，不存在"既有行为"），存的值本就是 AI 想表达的均线值个数

同时用迁移后的 DSL 重渲染 code 展示文本（ChatMessage 里的历史代码快照不动）。

用法（在 server/ 下）：
    .venv/bin/python -m scripts.migrate_ma_trend_lookback          # 试跑，只打印
    .venv/bin/python -m scripts.migrate_ma_trend_lookback --apply  # 写库
"""

from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import SessionLocal
from app.models.strategy import Strategy
from app.services.dsl import validate_dsl
from app.services.strategy_ai import _render_code


def migrate(apply: bool) -> None:
    db = SessionLocal()
    changed = 0
    try:
        for s in db.execute(select(Strategy)).scalars():
            tech = (s.dsl or {}).get("technical") or []
            trends = [t for t in tech if t.get("type") == "ma_trend"]
            if not trends:
                continue
            before = [(t["window"], t["lookback"]) for t in trends]
            for t in trends:
                if int(t["lookback"]) > int(t["window"]):
                    t["lookback"] = int(t["lookback"]) - int(t["window"]) + 1
            after = [(t["window"], t["lookback"]) for t in trends]
            if before == after:
                continue
            print(f"strategy #{s.id} {s.name!r}: lookback {before} -> {after}")
            changed += 1
            if apply:
                s.dsl = validate_dsl(s.dsl)  # 重新夹紧 + 规范化
                flag_modified(s, "dsl")
                if s.code:
                    s.code = _render_code(s.dsl)
        if apply:
            db.commit()
            print(f"已写库：{changed} 个策略")
        else:
            print(f"试跑结束：{changed} 个策略待迁移（加 --apply 写库）")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="实际写库（默认试跑）")
    migrate(parser.parse_args().apply)
