"""每日叙事打分任务（research PLAN Step 4，只前向记录）。

盘后 cron 触发。幂等：当日（CST）已有记录即跳过；无 DeepSeek key、
无新闻源、打分失败都静默跳过——前向序列宁缺毋滥。
依赖板块名单（sector_info），行业模块尚未落数据时不跑。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.sector import SectorInfo, SectorNarrative
from app.services.narrative import fetch_headlines, score_narrative

logger = logging.getLogger("kairos.narrative")


def update_narrative() -> None:
    if not settings.deepseek_api_key or settings.ai_provider not in ("auto", "deepseek"):
        return
    db = SessionLocal()
    try:
        cst_today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
        day = datetime(cst_today.year, cst_today.month, cst_today.day)
        exists = db.execute(
            select(func.count()).select_from(SectorNarrative).where(SectorNarrative.date == day)
        ).scalar()
        if exists:
            return
        sectors = db.execute(select(SectorInfo.code, SectorInfo.name)).all()
        if not sectors:
            logger.info("No sectors yet; narrative skipped")
            return
        name_to_code = {name: code for code, name in sectors}

        headlines = fetch_headlines()
        if not headlines:
            logger.info("No headlines fetched; narrative skipped")
            return
        result = score_narrative(headlines, list(name_to_code))
        if result is None:
            return

        m = result["market"]
        db.add(
            SectorNarrative(
                date=day, code="_market", direction=m["direction"], strength=m["strength"],
                summary=m["summary"], evidence=[], model=settings.deepseek_model,
            )
        )
        for s in result["sectors"]:
            db.add(
                SectorNarrative(
                    date=day, code=name_to_code[s["name"]], direction=s["direction"],
                    strength=s["strength"], summary=s["summary"], evidence=s["evidence"],
                    model=settings.deepseek_model,
                )
            )
        db.commit()
        logger.info(
            "Narrative for %s: %d sectors scored from %d headlines",
            cst_today, len(result["sectors"]), len(headlines),
        )
    except Exception as exc:  # noqa: BLE001 — keep the scheduler alive
        logger.warning("Narrative update failed: %s", exc)
    finally:
        db.close()
