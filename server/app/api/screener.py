from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas import ScreenerIn
from app.services.screener import run_classic

router = APIRouter(prefix="/screener", tags=["screener"])


@router.post("/run")
def run(body: ScreenerIn, db: Session = Depends(get_db)):
    return run_classic(
        db,
        industry=body.industry,
        pe_min=body.peMin,
        pe_max=body.peMax,
        roe_min=body.roeMin,
        page=body.page,
        page_size=body.pageSize,
    )
