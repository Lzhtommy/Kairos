"""LLM 叙事因子管道（research PLAN Step 4）：每日财经快讯标题 → DeepSeek
打「叙事方向/强度」分 → 落库。

铁律：**只前向记录，永不回测**——LLM 训练数据里见过历史新闻及其后市走势，
任何历史回测都天然虚高。攒满 12 个月前向样本后评估 IC ≥ 0.05 才进模型，
此前仅作展示层信息（读法与拥挤度配对：叙事升温 × 不拥挤 = 机会；
叙事沸腾 × 高拥挤 = 出口）。

新闻源经 akshare（生产镜像 WITH_DATA=true 自带）：财联社电报 + 东财全球
快讯，任一可用即可；打分走 DeepSeek JSON mode（与 strategy_ai 同配置），
失败静默跳过当日（前向序列缺一天无妨，绝不能写进猜测值）。
"""

from __future__ import annotations

import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("kairos.narrative")

MAX_HEADLINES = 120
MAX_EVIDENCE = 5


def fetch_headlines() -> list[str]:
    """拉当日财经快讯标题。

    主源东财全球快讯（快、稳定）；财联社电报只在主源空手时才试——
    该接口 2026-08 起 404，且 akshare 内部重试会挂近十分钟，
    不能让它挡在每日任务的必经路上。
    """

    def _from_em() -> list[str]:
        import akshare as ak  # noqa: PLC0415 — 可选依赖

        df = ak.stock_info_global_em()
        col = "标题" if "标题" in df.columns else df.columns[0]
        return [str(t).strip() for t in df[col].tolist() if str(t).strip()]

    def _from_cls() -> list[str]:
        import akshare as ak  # noqa: PLC0415

        df = ak.stock_info_global_cls()
        col = "标题" if "标题" in df.columns else df.columns[0]
        return [str(t).strip() for t in df[col].tolist() if str(t).strip()]

    titles: list[str] = []
    for fetch in (_from_em, _from_cls):
        try:
            titles = fetch()
        except Exception as exc:  # noqa: BLE001 — 单源失败再试下一个
            logger.info("Headline source %s unavailable: %s", fetch.__name__, exc)
        if titles:
            break

    seen: set[str] = set()
    out: list[str] = []
    for t in titles:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:MAX_HEADLINES]


def score_narrative(headlines: list[str], sector_names: list[str]) -> dict | None:
    """DeepSeek 批量打分。返回 {"market": {...}, "sectors": [{name, direction,
    strength, summary, evidence:[标题…]}]}；任何失败返回 None（当日跳过）。"""
    if not settings.deepseek_api_key or settings.ai_provider not in ("auto", "deepseek"):
        return None
    if not headlines:
        return None
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(headlines))
    prompt = (
        "你是 A 股行业叙事分析师。下面是今天的财经快讯标题和行业板块名单。\n"
        "任务：判断哪些行业今天有真实的叙事（政策、产业事件、供需变化等），"
        "逐行业给出方向和强度。只报有实质叙事的行业，没有就给空列表；"
        "不要因为行业名出现在新闻里就凑数。\n"
        "输出严格 JSON：{\"market\": {\"direction\": -1~1 的小数, \"strength\": 0~1 的小数, "
        "\"summary\": \"一句话概括今日整体叙事\"}, \"sectors\": [{\"name\": \"必须与板块名单完全一致\", "
        "\"direction\": -1~1（利空到利好）, \"strength\": 0~1（提及一次≈0.2，多条重磅政策≈0.9）, "
        "\"summary\": \"一句话\", \"evidence\": [支撑该判断的标题编号]}]}\n\n"
        f"行业板块名单：{'、'.join(sector_names)}\n\n"
        f"今日快讯标题：\n{numbered}"
    )
    try:
        r = httpx.post(
            f"{settings.deepseek_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            json={
                "model": settings.deepseek_model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "max_tokens": 2048,
                "temperature": 0,
            },
            timeout=60,
        )
        r.raise_for_status()
        payload = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001 — 打分失败当日跳过，不写猜测值
        logger.warning("Narrative scoring failed: %s", exc)
        return None

    def _clip(v, lo: float, hi: float) -> float:
        try:
            return max(lo, min(hi, float(v)))
        except (TypeError, ValueError):
            return 0.0

    name_set = set(sector_names)
    sectors = []
    for item in payload.get("sectors") or []:
        name = str(item.get("name") or "")
        if name not in name_set:
            continue
        evidence = [
            headlines[i]
            for i in (item.get("evidence") or [])
            if isinstance(i, int) and 0 <= i < len(headlines)
        ][:MAX_EVIDENCE]
        strength = _clip(item.get("strength"), 0, 1)
        # 模型倾向于把全部行业都报一遍（"无直接叙事" 强度 0.1）——
        # 无证据且强度不足的行是噪声，不落库
        if not evidence and strength < 0.2:
            continue
        sectors.append(
            {
                "name": name,
                "direction": _clip(item.get("direction"), -1, 1),
                "strength": strength,
                "summary": str(item.get("summary") or ""),
                "evidence": evidence,
            }
        )
    market = payload.get("market") or {}
    return {
        "market": {
            "direction": _clip(market.get("direction"), -1, 1),
            "strength": _clip(market.get("strength"), 0, 1),
            "summary": str(market.get("summary") or ""),
        },
        "sectors": sectors,
    }
