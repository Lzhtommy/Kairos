from collections.abc import Generator

from sqlalchemy import DateTime, Float, create_engine, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")


# MySQL 方言默认把 Float 编译成 4 字节 FLOAT（约 7 位有效数字，市值 / 成交额会丢精度），
# DateTime 编译成不带小数秒的 DATETIME（微秒被四舍五入，可能进位到下一秒）。
# 统一成 SQLite 下的实际语义：双精度 + 微秒。
@compiles(Float, "mysql")
def _mysql_double(_type, _compiler, **_kw) -> str:  # noqa: ANN001
    return "DOUBLE"


@compiles(DateTime, "mysql")
def _mysql_datetime6(_type, _compiler, **_kw) -> str:  # noqa: ANN001
    return "DATETIME(6)"


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


def upsert_rows(db: Session, model: type[Base], rows: list[dict], batch: int = 1000) -> None:
    """按主键批量 upsert，每批一条语句。

    替代逐行 db.merge：merge 每行先 SELECT 再 UPDATE，RDS 一次往返约 5ms，
    全市场 5500 只一轮要一分钟以上。只适用于除主键外没有唯一键的表
    （MySQL 的 ON DUPLICATE KEY 对任一唯一键冲突都会触发更新）。
    """
    if not rows:
        return
    table = model.__table__
    pk = {c.name for c in table.primary_key.columns}
    dialect = db.get_bind().dialect.name
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        cols = [c for c in chunk[0] if c not in pk]
        if dialect == "mysql":
            from sqlalchemy.dialects.mysql import insert as mysql_insert

            stmt = mysql_insert(table).values(chunk)
            stmt = stmt.on_duplicate_key_update({c: stmt.inserted[c] for c in cols})
        else:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(table).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=sorted(pk), set_={c: stmt.excluded[c] for c in cols}
            )
        db.execute(stmt)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
