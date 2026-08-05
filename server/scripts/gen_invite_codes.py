"""生成注册邀请码（一码一用）。

用法（在 server/ 下）：
    .venv/bin/python -m scripts.gen_invite_codes           # 生成 1 个
    .venv/bin/python -m scripts.gen_invite_codes -n 10     # 生成 10 个
    .venv/bin/python -m scripts.gen_invite_codes --list    # 查看所有码及使用状态
"""

from __future__ import annotations

import argparse
import secrets

from sqlalchemy import select

from app.core.db import Base, SessionLocal, engine
from app.models.user import InviteCode, User


def gen(n: int) -> None:
    with SessionLocal() as db:
        codes = []
        for _ in range(n):
            code = secrets.token_urlsafe(9)  # 12 字符，够随机也方便手输
            db.add(InviteCode(code=code))
            codes.append(code)
        db.commit()
        for code in codes:
            print(code)


def list_codes() -> None:
    with SessionLocal() as db:
        rows = db.execute(select(InviteCode).order_by(InviteCode.id)).scalars().all()
        if not rows:
            print("（还没有邀请码）")
            return
        for c in rows:
            if c.used_by is None:
                print(f"{c.code}  未使用")
            else:
                email = db.get(User, c.used_by)
                who = email.email if email else f"user#{c.used_by}"
                used_at = c.used_at.strftime("%Y-%m-%d %H:%M") if c.used_at else "?"
                print(f"{c.code}  已用于 {who} @ {used_at}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", type=int, default=1, help="生成数量")
    parser.add_argument("--list", action="store_true", help="列出所有邀请码")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)  # 服务还没启动过新代码时，表可能不存在
    if args.list:
        list_codes()
    else:
        gen(args.n)
