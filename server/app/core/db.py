from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

import kairos_data.store  # noqa: F401  (MySQL 下 Float→DOUBLE、DateTime→DATETIME(6))
from app.core.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
    # MySQL 服务端 wait_timeout 会断开闲置连接；回收早于它
    pool_recycle=-1 if _is_sqlite else 1800,
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        # 采集 / 研报 / 策略任务和 API 并发写时排队等锁，而不是立即抛 database is locked
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
