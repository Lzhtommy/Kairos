"""MCP 数据服务入口：https://kairos.ren/api/mcp（Streamable HTTP，无状态，JSON 响应）。

鉴权：请求头 Authorization: Bearer <API Key>（或 X-API-Key）。Key 在设置页自助创建，见 app.services.api_keys。
每次工具调用计量（每分钟 + 每日上限），超限以工具错误返回给 agent，不断开连接。

无状态 + JSON 响应：每个请求独立，不依赖进程内会话，单 worker、重启、nginx 反代都不受影响。
"""

import contextvars
import functools
import inspect
import json
import logging
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.types import Receive, Scope, Send

from app.core.db import SessionLocal
from app.mcp_server import tools
from app.services import api_keys

logger = logging.getLogger("kairos.mcp")

INSTRUCTIONS = """Kairos A 股数据服务：沪深北 A 股（含退市）、ETF、可转债的行情与日 K、每日估值指标、
财报与三大报表、分红股本股东、两融龙虎榜大宗交易、公告、全球财经快讯、宏观利率。
先用 search_securities 把名称换成 6 位代码；时间序列工具按日期正序返回最近 limit 行，
列式 {"columns", "rows"}。日期一律 YYYY-MM-DD，北京时间。数据仅供研究，不构成投资建议。"""

_current_key: contextvars.ContextVar[int | None] = contextvars.ContextVar("mcp_api_key", default=None)

mcp = FastMCP(
    "Kairos A 股数据",
    instructions=INSTRUCTIONS,
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    # 公网服务靠 API Key 鉴权；DNS rebinding 防护是给本机服务用的，开着会拒掉 Host: kairos.ren
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _run(fn, key_id: int, kwargs: dict[str, Any]) -> str:  # noqa: ANN001
    db = SessionLocal()
    try:
        api_keys.meter(db, key_id)
        # 统一序列化成一段 JSON 文本：列表返回值否则会被拆成多个内容块
        return json.dumps(fn(db, **kwargs), ensure_ascii=False, separators=(",", ":"))
    except api_keys.QuotaExceeded as exc:
        raise ToolError(str(exc)) from exc
    except tools.UserError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        logger.exception("MCP 工具 %s 出错（key %s）", fn.__name__, key_id)
        raise ToolError("服务内部错误，请稍后再试") from exc
    finally:
        db.close()


def _register(fn) -> None:  # noqa: ANN001
    """把 tools 里的 fn(db, **参数) 包成异步工具：去掉 db 参数，计量 + 查库放到线程池（不阻塞事件循环）。"""
    sig = inspect.signature(fn)
    params = [p for name, p in sig.parameters.items() if name != "db"]

    @functools.wraps(fn)
    async def tool(**kwargs: Any) -> str:
        key_id = _current_key.get()
        if key_id is None:  # 走不到：ASGI 层已拒绝无 Key 请求
            raise ToolError("缺少 API Key")
        return await anyio.to_thread.run_sync(functools.partial(_run, fn, key_id, kwargs))

    del tool.__wrapped__  # 否则 inspect.signature 会顺着它拿到带 db 的原签名
    tool.__signature__ = sig.replace(parameters=params)
    tool.__annotations__ = {k: v for k, v in fn.__annotations__.items() if k not in ("db", "return")}
    mcp.add_tool(tool, name=fn.__name__, description=inspect.getdoc(fn), structured_output=False,
                 annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))


for _fn in tools.TOOLS:
    _register(_fn)

mcp.streamable_http_app()  # 创建 session_manager（惰性），返回的 Starlette 应用不用：鉴权由下面的 ASGI 层做


def _header(scope: Scope, name: bytes) -> str:
    for k, v in scope.get("headers") or []:
        if k == name:
            return v.decode("latin-1").strip()
    return ""


def _verify(plain: str) -> int | None:
    db = SessionLocal()
    try:
        key = api_keys.lookup(db, plain)
        return key.id if key else None
    finally:
        db.close()


async def _deny(send: Send, message: str) -> None:
    body = json.dumps({"error": message}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": 401, "headers": [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"www-authenticate", b'Bearer realm="kairos"'),
    ]})
    await send({"type": "http.response.body", "body": body})


async def _endpoint(scope: Scope, receive: Receive, send: Send) -> None:
    """挂在 /api/mcp 的 ASGI 端点：校验 API Key 后交给 MCP 会话管理器。"""
    auth = _header(scope, b"authorization")
    plain = auth[7:].strip() if auth.lower().startswith("bearer ") else _header(scope, b"x-api-key")
    if not plain:
        await _deny(send, "缺少 API Key：请求头加 Authorization: Bearer <你的 Key>，在 Kairos 设置页创建")
        return
    key_id = await anyio.to_thread.run_sync(_verify, plain)
    if key_id is None:
        await _deny(send, "API Key 无效或已吊销")
        return
    token = _current_key.set(key_id)
    try:
        await mcp.session_manager.handle_request(scope, receive, send)
    finally:
        _current_key.reset(token)


class _Asgi:
    """Starlette 的 Route 把普通函数当成 request 处理函数，ASGI 端点得是可调用对象。"""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await _endpoint(scope, receive, send)


app = _Asgi()
