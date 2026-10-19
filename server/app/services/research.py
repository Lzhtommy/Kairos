"""研报流水线编排：采集 → 事实表 → 评分 → 撰写 → 落库（docs/RESEARCH-DESIGN.md §5）。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.market import FactorSnapshot, Fundamental, Quote, StockInfo
from app.models.research import ResearchRawCache, ResearchReport
from app.services.research_annual import select_blocks, split_blocks
from app.services.research_data import ANNUAL_LABEL, fetch_all, fetch_annual_mdna
from app.services.research_facts import build_facts, is_financial
from app.services.research_score import score
from app.services.research_writer import EXTRACT_VERSION, extract_annual, llm_enabled, render_markdown, write_sections

logger = logging.getLogger("kairos.research")

HIST_LIMIT = 750  # 约 3 年交易日


def valuation_inputs(db: Session, code: str, industry: str) -> dict:
    """估值输入全部来自本地行情库：最新快照 + 自身历史 + 同行业最新截面。"""
    latest = db.execute(
        select(FactorSnapshot).where(FactorSnapshot.code == code).order_by(FactorSnapshot.ts.desc()).limit(1)
    ).scalar_one_or_none()
    out: dict = {}
    if latest is not None:
        out = {
            "pe": latest.pe, "pb": latest.pb, "dividend_yield": latest.dividend_yield,
            "market_cap": latest.market_cap, "as_of": latest.ts.strftime("%Y-%m-%d"),
        }
    else:
        q = db.get(Quote, code)
        fnd = db.get(Fundamental, code)
        if q is not None:
            out = {"pe": q.pe, "pb": q.pb, "market_cap": q.market_cap, "as_of": q.ts.strftime("%Y-%m-%d")}
        if fnd is not None:
            out["dividend_yield"] = fnd.dividend_yield

    # 库里股息率存小数（0.0388 = 3.88%），事实表统一用百分数
    if out.get("dividend_yield") is not None:
        out["dividend_yield"] = out["dividend_yield"] * 100

    hist = db.execute(
        select(FactorSnapshot.pe, FactorSnapshot.pb)
        .where(FactorSnapshot.code == code)
        .order_by(FactorSnapshot.ts.desc())
        .limit(HIST_LIMIT)
    ).all()
    out["pe_hist"] = [pe for pe, _ in hist if pe is not None]
    out["pb_hist"] = [pb for _, pb in hist if pb is not None]

    if industry and industry != "—":
        last_ts = db.execute(select(func.max(FactorSnapshot.ts))).scalar()
        if last_ts is not None:
            out["industry_pes"] = list(
                db.execute(
                    select(FactorSnapshot.pe).where(
                        FactorSnapshot.ts == last_ts, FactorSnapshot.industry == industry, FactorSnapshot.pe.is_not(None)
                    )
                ).scalars()
            )
    return out


def annual_insights(db: Session, code: str, progress) -> tuple[dict | None, dict, int]:
    """年报深读（§10）：MD&A 文本 → 选块 → LLM 抽取 + 原文校验。返回 (annual | None, source, tokens)。

    抽取结果按「公告 ID + EXTRACT_VERSION」缓存，重新生成报告不重复花 LLM。失败一律降级为 None。
    """
    if not llm_enabled():
        # 抽取离不开 LLM；没配 key 就不下载 PDF
        return None, {"key": "annual", "label": ANNUAL_LABEL, "fetched_at": None, "status": "failed",
                      "error": "未配置 AI，跳过年报深读"}, 0
    payload, source = fetch_annual_mdna(db, code, progress)
    if payload is None:
        return None, source, 0
    meta = payload["report"]
    cache_tag = f"{meta['announcement_id']}:{EXTRACT_VERSION}"
    cached = db.get(ResearchRawCache, ("annual_extract", code))
    if cached and (cached.payload or {}).get("tag") == cache_tag:
        return {**cached.payload["extract"], "report": meta}, source, 0

    progress("年报深读：抽取公司解释")
    pages = [(int(no), text) for no, text in payload["pages"]]
    try:
        extract, tokens = extract_annual(select_blocks(split_blocks(pages)), pages)
    except Exception as exc:  # noqa: BLE001 — 抽取失败不影响报告主体
        logger.warning("Research annual extract %s failed: %s", code, exc)
        return None, {**source, "error": f"抽取失败：{type(exc).__name__}"}, 0
    now = datetime.now(timezone.utc)
    if cached:
        cached.payload, cached.fetched_at = {"tag": cache_tag, "extract": extract}, now
    else:
        db.add(ResearchRawCache(source="annual_extract", code=code,
                                payload={"tag": cache_tag, "extract": extract}, fetched_at=now))
    db.commit()
    return {**extract, "report": meta}, source, tokens


def run_report(db: Session, report: ResearchReport) -> None:
    """执行一条 running 状态的报告；异常由调用方兜底标 failed。"""

    def progress(text: str) -> None:
        report.progress = text[:64]
        db.commit()

    info = db.get(StockInfo, report.code)
    name = info.name if info else report.name
    industry = info.industry if info else report.industry
    report.name, report.industry = name, industry

    raw, sources = fetch_all(db, report.code, progress)
    csrc_industry = ((raw.get("profile") or [{}])[0] or {}).get("所属行业")
    if is_financial(industry, csrc_industry):
        raise ValueError(f"金融类股票（{csrc_industry or industry}）暂不支持")
    core = [s for s in sources if s["key"] in ("income", "balance", "abstract_annual") and s["status"] == "failed"]
    if len(core) == 3:
        raise RuntimeError("核心财报数据源全部不可用：" + "；".join(s.get("error") or "" for s in core))

    progress("计算事实表与评分")
    dataset = build_facts(raw, valuation_inputs(db, report.code, industry), name)
    result = score(dataset)

    annual, annual_source, annual_tokens = annual_insights(db, report.code, progress)
    sources.append(annual_source)

    sections, tokens = write_sections(dataset, result, name, report.code, progress, annual=annual)
    tokens += annual_tokens

    report.data_as_of = dataset["data_as_of"]
    report.scoring_version = result["scoring_version"]
    report.total_score = result["total_score"]
    report.grade = result["grade"]
    report.coverage = result["coverage"]
    report.sub_scores = result["sub_scores"]
    report.red_flags = result["red_flags"]
    report.facts = dataset["facts"]
    report.sections = sections
    report.sources = sources
    report.llm_model = settings.deepseek_model if any(s.get("ai") for s in sections) else None
    report.llm_tokens = tokens
    report.content_md = render_markdown({
        "name": name, "code": report.code, "industry": industry, "data_as_of": report.data_as_of,
        "total_score": report.total_score, "grade": report.grade, "coverage": report.coverage,
        "sub_scores": report.sub_scores, "red_flags": report.red_flags, "sections": sections,
        "sources": sources, "scoring_version": report.scoring_version,
    })
    report.status = "done"
    report.progress = "完成"
    report.error = None
    report.finished_at = datetime.now(timezone.utc)
    db.commit()
