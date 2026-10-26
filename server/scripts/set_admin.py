"""设置 / 撤销管理员。

用法（在 server/ 下）：
    .venv/bin/python -m scripts.set_admin someone            # 提升为管理员
    .venv/bin/python -m scripts.set_admin someone --revoke   # 撤销
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.user import User

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", help="用户名")
    parser.add_argument("--revoke", action="store_true", help="撤销管理员")
    args = parser.parse_args()

    with SessionLocal() as db:
        name = args.username.strip().lower()
        user = db.execute(select(User).where(User.username == name)).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"找不到用户：{args.username}")
        user.is_admin = not args.revoke
        db.commit()
        print(f"{user.username} 现在{'是' if user.is_admin else '不是'}管理员")
