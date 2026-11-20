"""LLM 叙事因子管道（research PLAN Step 4）：每日财经快讯标题 → DeepSeek
打「叙事方向/强度」分 → 落库。

铁律：**只前向记录，永不回测**——LLM 训练数据里见过历史新闻及其后市走势，
任何历史回测都天然虚高。攒满 12 个月前向样本后评估 IC ≥ 0.05 才进模型，
此前仅作展示层信息（读法与拥挤度配对：叙事升温 × 不拥挤 = 机会；
叙事沸腾 × 高拥挤 = 出口）。

新闻源是自家采集的 news_item（财联社 / 金十 / 华尔街见闻 / 新浪 / 同花顺
合并去重，见 services/news.py），库里不够时回落 akshare 东财全球快讯；
打分走 DeepSeek JSON mode（与 strategy_ai 同配置），失败静默跳过当日（前向序列缺一天无妨，绝不能写进猜测值）。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.services import llm, news

logger = logging.getLogger("kairos.narrative")

MAX_HEADLINES = 120
MAX_EVIDENCE = 5


MIN_DB_HEADLINES = 30


def fetch_headlines(db: Session | None = None) -> list[str]:
    """近 24 小时的快讯标题，每个事件一条。

    主源是自家采集的 news_item（五源合并、按重要度挑）；库里不够
    （采集刚上线 / 被关掉）时回落东财全球快讯。财联社的 akshare 接口
    2026-08 起 404 且会挂近十分钟，已不再使用。
    """
    if db is not None:
        titles = news.recent_headlines(db, limit=MAX_HEADLINES)
        if len(titles) >= MIN_DB_HEADLINES:
            return titles
        logger.info("news_item has only %d headlines in 24h; falling back to akshare", len(titles))

    try:
        import akshare as ak  # noqa: PLC0415 — 可选依赖

        df = ak.stock_info_global_em()
        col = "标题" if "标题" in df.columns else df.columns[0]
        titles = [str(t).strip() for t in df[col].tolist() if str(t).strip()]
    except Exception as exc:  # noqa: BLE001
        logger.info("Headline source akshare em unavailable: %s", exc)
        return []

    return list(dict.fromkeys(titles))[:MAX_HEADLINES]


def score_narrative(headlines: list[str], sector_names: list[str]) -> dict | None:
    """LLM 批量打分。返回 {"market": {...}, "sectors": [{name, direction,
    strength, summary, evidence:[标题…]}]}；任何失败返回 None（当日跳过）。"""
    if not llm.enabled() or not headlines:
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
        payload, _tokens = llm.chat_json([{"role": "user", "content": prompt}], max_tokens=2048, timeout=60)
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
