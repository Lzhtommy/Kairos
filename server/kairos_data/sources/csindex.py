"""中证指数官网：中证系列指数的当期成分与权重（每月更新，只有当期名单，历史需自行积累）。

文件是 xls，解析需要可选依赖 pandas + xlrd。
"""

from __future__ import annotations

import io
from datetime import date

import httpx

_URL = "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/closeweight/{code}closeweight.xls"


def fetch_weights(index_code: str) -> list[dict]:
    """返回 [{date, code, weight}]，code 补齐 6 位，weight 单位 %。"""
    import pandas as pd  # 可选依赖

    r = httpx.get(_URL.format(code=index_code), headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    r.raise_for_status()
    df = pd.read_excel(io.BytesIO(r.content), dtype=str)
    day_col = next(c for c in df.columns if c.startswith("日期"))
    code_col = next(c for c in df.columns if c.startswith("成份券代码"))
    weight_col = next(c for c in df.columns if c.startswith("权重"))
    out = []
    for d, code, w in zip(df[day_col], df[code_col], df[weight_col], strict=True):
        if not isinstance(d, str) or not isinstance(code, str):
            continue
        out.append({"date": date(int(d[:4]), int(d[4:6]), int(d[6:8])), "code": code.strip().zfill(6),
                    "weight": float(w) if isinstance(w, str) and w.strip() else None})
    return out
