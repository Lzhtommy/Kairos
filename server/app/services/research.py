"""研报流水线编排：采集 → 事实表 → 评分 → 撰写 → 落库（docs/RESEARCH-DESIGN.md §5）。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import Fundamental, Quote, StockInfo
from app.models.research import ResearchRawCache, ResearchReport
from app.models.warehouse import DailyBasic, FinReport
from app.services import llm
from app.services.pit import PitFundamentals
from app.services.research_annual import REPORT_KINDS, choose_reports, merge_extracts, report_blocks
from app.services.research_data import fetch_all, fetch_report_text, latest_report_meta, report_source
from app.services.research_facts import build_facts, is_financial
from app.services.research_score import score
from app.services.research_writer import EXTRACT_VERSION, extract_periodic, llm_enabled, render_markdown, write_sections

logger = logging.getLogger("kairos.research")

HIST_LIMIT = 2430  # 自身历史分位回看：约 10 年交易日
INDUSTRY_FIN_DAYS = 800  # 同业财报回看：覆盖最新报告期及其上年同期


def valuation_inputs(db: Session, code: str, industry: str) -> dict:
    """估值输入全部来自本地每日指标库（daily_basic）：最新一行 + 自身历史 + 同行业同日截面。
    ROE / 股息率按当时已公告的财报与分红算（app.services.pit）。"""
    hist = db.execute(
        select(DailyBasic.trade_date, DailyBasic.pe_ttm, DailyBasic.pb_mrq, DailyBasic.close, DailyBasic.total_mv)
        .where(DailyBasic.code == code, DailyBasic.suspended.is_(False))
        .order_by(DailyBasic.trade_date.desc())
        .limit(HIST_LIMIT)
    ).all()
    out: dict = {}
    if hist:
        day, pe, pb, close, mv = hist[0]
        pit = PitFundamentals(db, day, day, codes=[code])
        dy = pit.dividend_yield(code, day, close)
        out = {
            "pe": pe, "pb": pb, "dividend_yield": dy, "market_cap": mv / 1e8 if mv else None, "price": close,
            "as_of": day.isoformat(), "hist_source": "daily_basic", "hist_as_of": day.isoformat(),
            "pe_hist": [v for _, v, *_ in hist if v is not None],
            "pb_hist": [v for _, _, v, *_ in hist if v is not None],
        }
    else:  # 新股 / 每日指标还没写入：退回实时行情
        q = db.get(Quote, code)
        fnd = db.get(Fundamental, code)
        if q is not None:
            out = {"pe": q.pe, "pb": q.pb, "market_cap": q.market_cap, "price": q.price,
                   "as_of": q.ts.strftime("%Y-%m-%d")}
        if fnd is not None:
            out["dividend_yield"] = fnd.dividend_yield

    # 库里股息率存小数（0.0388 = 3.88%），事实表统一用百分数
    if out.get("dividend_yield") is not None:
        out["dividend_yield"] = out["dividend_yield"] * 100

    out["self_code"], out["industry"] = code, industry
    if hist and industry and industry != "—":
        out.update(_industry_peers(db, hist[0][0], industry))
    return out


def _industry_peers(db: Session, day: date, industry: str) -> dict:
    """同行业（stock_info 当前行业）在本股最新交易日的截面：trade_date 索引取当日约 5900 行，再按行业筛。"""
    rows = db.execute(
        select(DailyBasic.code, DailyBasic.pe_ttm, DailyBasic.pb_mrq, DailyBasic.close, DailyBasic.total_mv,
               StockInfo.name)
        .join(StockInfo, StockInfo.code == DailyBasic.code)
        .where(DailyBasic.trade_date == day, StockInfo.industry == industry)
    ).all()
    codes = [r[0] for r in rows]
    pit = PitFundamentals(db, day, day, codes=codes)
    peers = []
    for c, pe, pb, close, mv, name in rows:
        dy = pit.dividend_yield(c, day, close)
        peers.append({"code": c, "name": (name or c).replace(" ", ""), "market_cap": mv / 1e8 if mv else None,
                      "pe": pe, "pb": pb, "roe": pit.roe(c, day),
                      "dividend_yield": None if dy is None else round(dy * 100, 2)})  # 股息率转百分数
    return {
        "peers_as_of": day.isoformat(),
        "industry_pes": [p["pe"] for p in peers if p["pe"] is not None],
        "industry_rows": peers,
        "industry_fin": _industry_fin(db, codes),
    }


def _industry_fin(db: Session, codes: list[str]) -> list[dict]:
    """同行业公司近两年各报告期的营收 / 归母净利润及同比（fin_report），给行业增速对比用。"""
    if not codes:
        return []
    since = date.today() - timedelta(days=INDUSTRY_FIN_DAYS)
    rows = db.execute(
        select(FinReport.code, FinReport.report_date, FinReport.revenue, FinReport.net_profit,
               FinReport.revenue_yoy, FinReport.profit_yoy)
        .where(FinReport.code.in_(codes), FinReport.report_date >= since)
    ).all()
    return [
        {"code": c, "report_date": d.isoformat(), "revenue": rev, "net_profit": np_,
         "revenue_yoy": ry, "profit_yoy": py}
        for c, d, rev, np_, ry, py in rows
    ]


def _extract_cached(db: Session, code: str, kind: str, payload: dict, progress) -> tuple[dict | None, int, str | None]:
    """一份报告的抽取结果，按「公告 ID + EXTRACT_VERSION」缓存。返回 (extract | None, tokens, error)。"""
    meta = payload["report"]
    tag = f"{meta['announcement_id']}:{EXTRACT_VERSION}"
    cached = db.get(ResearchRawCache, (f"{kind}_extract", code))
    if cached and (cached.payload or {}).get("tag") == tag:
        return cached.payload["extract"], 0, None
    doc = meta.get("doc") or "定期报告"
    progress(f"深读{doc}：抽取公司解释")
    pages = [(int(no), text) for no, text in payload["pages"]]
    try:
        extract, tokens = extract_periodic(report_blocks(kind, pages), pages, doc)
    except Exception as exc:  # noqa: BLE001 — 抽取失败不影响报告主体
        logger.warning("Research %s extract %s failed: %s", kind, code, exc)
        return None, 0, f"抽取失败：{type(exc).__name__}"
    now = datetime.now(timezone.utc)
    if cached:
        cached.payload, cached.fetched_at = {"tag": tag, "extract": extract}, now
    else:
        db.add(ResearchRawCache(source=f"{kind}_extract", code=code,
                                payload={"tag": tag, "extract": extract}, fetched_at=now))
    db.commit()
    return extract, tokens, None


def periodic_insights(db: Session, code: str, progress) -> tuple[dict | None, list[dict], int]:
    """定期报告深读（§10）：选报告（年报 + 更新的半年报 + 更新的季报）→ 切文本 → LLM 抽取 + 原文校验 → 合并。

    返回 (insights | None, sources, tokens)。任一份失败只影响那一份，全部失败降级为 None。
    """
    if not llm_enabled():
        # 抽取离不开 LLM；没配 key 就不查不下载
        return None, [report_source("annual", None, "failed", None, "未配置 AI，跳过定期报告深读")], 0
    progress("查询定期报告")
    metas: dict[str, dict | None] = {}
    annual_list_failed = False
    for kind in REPORT_KINDS:
        try:
            metas[kind] = latest_report_meta(code, kind)
        except Exception as exc:  # noqa: BLE001
            logger.info("Research %s list %s failed: %s", kind, code, exc)
            metas[kind] = None
            annual_list_failed |= kind == "annual"
    chosen = choose_reports(metas)
    if annual_list_failed:
        chosen.append("annual")  # 列表查不到时仍可回退年报旧缓存

    sources: list[dict] = []
    docs: list[tuple[dict, dict]] = []
    tokens = 0
    for kind in chosen:
        payload, src = fetch_report_text(db, code, kind, metas.get(kind), progress)
        if payload is not None:
            extract, used, err = _extract_cached(db, code, kind, payload, progress)
            tokens += used
            if err:
                src = {**src, "error": err}
            if extract is not None:
                docs.append((payload["report"], extract))
        sources.append(src)
    if not docs:
        return None, sources, tokens
    docs.sort(key=lambda d: d[0].get("period_end") or "", reverse=True)
    return merge_extracts(docs), sources, tokens


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

    periodic, periodic_sources, periodic_tokens = periodic_insights(db, report.code, progress)
    sources = sources + periodic_sources

    sections, tokens = write_sections(dataset, result, name, report.code, progress, periodic=periodic)
    tokens += periodic_tokens

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
    report.llm_model = llm.model_name() if any(s.get("ai") for s in sections) else None
    report.llm_tokens = tokens
    report.content_md = render_markdown({
        "name": name, "code": report.code, "industry": industry, "data_as_of": report.data_as_of,
        "total_score": report.total_score, "grade": report.grade, "coverage": report.coverage,
        "sub_scores": report.sub_scores, "red_flags": report.red_flags, "sections": sections,
        "sources": sources, "scoring_version": report.scoring_version, "facts": dataset["facts"],
    })
    report.status = "done"
    report.progress = "完成"
    report.error = None
    report.finished_at = datetime.now(timezone.utc)
    db.commit()
