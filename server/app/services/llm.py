"""统一的 LLM 调用层。业务代码只拼 prompt，走 chat_json() / stream_text()。

后端由 settings.ai_provider 选择：
- deepseek / auto：DeepSeek（OpenAI 兼容），默认关闭思考。生产 ECS 在大陆，Anthropic 被地域封锁，故为默认。
- openai：任意 OpenAI 兼容端点（OpenAI、通义、Kimi、智谱、豆包、硅基流动、OpenRouter、Ollama、vLLM…）。
- anthropic：Claude 原生 Messages API。
- rule：不调 LLM，各调用处回落到规则 / 跳过。

配置优先读 LLM_*，留空时回落到旧的 DEEPSEEK_*（兼容已有部署）。
LLM_EXTRA_BODY 是 JSON，浅合并进请求体；值为 null 表示删除该字段
（例如推理模型拒绝 temperature 时写 {"temperature": null}）。
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings

_PRESETS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "kind": "openai",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        # deepseek-flash 默认开思考：输出暴涨且 temperature 失效
        "extra": {"thinking": {"type": "disabled"}},
    },
    "openai": {"kind": "openai", "base_url": "https://api.openai.com/v1", "model": "", "extra": {}},
    "anthropic": {"kind": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-opus-5-5", "extra": {}},
}
_ANTHROPIC_OFFICIAL = "https://api.anthropic.com"


@dataclass(frozen=True)
class _Config:
    kind: str  # "openai" | "anthropic"
    base_url: str
    api_key: str
    model: str
    extra: dict[str, Any] = field(default_factory=dict)


def _config() -> _Config | None:
    name = settings.ai_provider.lower()
    if name == "auto":
        name = "deepseek"
    preset = _PRESETS.get(name)
    if preset is None:  # "rule" 或未知值
        return None
    legacy = name == "deepseek"
    api_key = settings.llm_api_key or (settings.deepseek_api_key if legacy else "")
    base_url = (settings.llm_base_url or (settings.deepseek_base_url if legacy else "") or preset["base_url"]).rstrip("/")
    model = settings.llm_model or (settings.deepseek_model if legacy else "") or preset["model"]
    # 自建 OpenAI 兼容端点（如本地 Ollama）可以不要 key，只要显式给了 base_url
    keyless_ok = name == "openai" and bool(settings.llm_base_url)
    if not model or not (api_key or keyless_ok):
        return None
    extra = dict(preset["extra"])
    if settings.llm_extra_body:
        extra.update(json.loads(settings.llm_extra_body))
    return _Config(preset["kind"], base_url, api_key, model, extra)


def enabled() -> bool:
    return _config() is not None


def model_name() -> str | None:
    cfg = _config()
    return cfg.model if cfg else None


def _require() -> _Config:
    cfg = _config()
    if cfg is None:
        raise RuntimeError("LLM 未配置")
    return cfg


def _merge(body: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for k, v in extra.items():
        if v is None:
            body.pop(k, None)
        else:
            body[k] = v
    return body


def parse_json(text: str) -> dict[str, Any]:
    """容忍 ```json 代码块和前后多余文字；仍解析失败时抛 json.JSONDecodeError。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


# ---------------------------------------------------------------- OpenAI 兼容


def _openai_request(cfg: _Config, messages, max_tokens, temperature, *, json_mode, stream) -> dict:
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    body: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if stream:
        body["stream"] = True
    return {"url": f"{cfg.base_url}/chat/completions", "headers": headers, "json": _merge(body, cfg.extra)}


# ---------------------------------------------------------------- Anthropic

# 当前 Claude 模型思考常开、不接受 temperature；思考 token 计入 max_tokens，故留出余量
_ANTHROPIC_THINKING_HEADROOM = 4096


def _anthropic_request(cfg: _Config, messages, max_tokens, *, json_mode, stream) -> dict:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    if json_mode:
        system = (system + "\n\n" if system else "") + "只输出一个 JSON 对象，不要输出任何其他文字或代码块标记。"
    turns: list[dict[str, str]] = []
    for m in messages:
        if m["role"] == "system":
            continue
        if turns and turns[-1]["role"] == m["role"]:  # API 要求 user / assistant 交替
            turns[-1] = {"role": m["role"], "content": turns[-1]["content"] + "\n\n" + m["content"]}
        elif turns or m["role"] == "user":  # 首条必须是 user
            turns.append({"role": m["role"], "content": m["content"]})
    body: dict[str, Any] = {
        "model": cfg.model,
        "messages": turns,
        "max_tokens": max_tokens + _ANTHROPIC_THINKING_HEADROOM,
        "output_config": {"effort": "low"},  # 都是抽取 / 改写类短任务
    }
    if system:
        body["system"] = system
    if stream:
        body["stream"] = True
    return _anthropic_envelope(cfg, body)


def _anthropic_envelope(cfg: _Config, body: dict[str, Any]) -> dict:
    headers = {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01"}
    if cfg.base_url == _ANTHROPIC_OFFICIAL:  # 安全分类器拒答时服务端自动换模型重跑；第三方代理未必认这个 beta
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        body["fallbacks"] = "default"
    return {"url": f"{cfg.base_url}/v1/messages", "headers": headers, "json": _merge(body, cfg.extra)}


def _anthropic_text(body: dict) -> str:
    if body.get("stop_reason") == "refusal":
        raise RuntimeError("Claude 拒绝了该请求")
    return "".join(b.get("text", "") for b in body.get("content") or [] if b.get("type") == "text")


# ---------------------------------------------------------------- 公共入口


def chat_json(
    messages: list[dict[str, str]], *, max_tokens: int = 1024, temperature: float = 0, timeout: float = 60
) -> tuple[dict[str, Any], int]:
    """同步调用，返回 (解析后的 JSON, 消耗 token 数)。网络 / HTTP / 解析错误原样抛出。"""
    cfg = _require()
    if cfg.kind == "anthropic":
        req = _anthropic_request(cfg, messages, max_tokens, json_mode=True, stream=False)
        r = httpx.post(**req, timeout=max(timeout, 120))
        r.raise_for_status()
        body = r.json()
        usage = body.get("usage") or {}
        tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        return parse_json(_anthropic_text(body)), tokens
    req = _openai_request(cfg, messages, max_tokens, temperature, json_mode=True, stream=False)
    r = httpx.post(**req, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
    return parse_json(body["choices"][0]["message"]["content"]), tokens


async def stream_text(
    messages: list[dict[str, str]], *, max_tokens: int = 1024, temperature: float = 0.3, timeout: float = 60
) -> AsyncIterator[str]:
    """流式输出纯文本增量。异常向上抛，由调用方降级。"""
    cfg = _require()
    if cfg.kind == "anthropic":
        req = _anthropic_request(cfg, messages, max_tokens, json_mode=False, stream=True)
    else:
        req = _openai_request(cfg, messages, max_tokens, temperature, json_mode=False, stream=True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
        async with client.stream("POST", **req) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                ev = json.loads(data)
                if cfg.kind == "anthropic":
                    if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                        yield ev["delta"]["text"]
                    elif ev.get("type") == "message_delta" and ev["delta"].get("stop_reason") == "refusal":
                        raise RuntimeError("Claude 拒绝了该请求")
                    elif ev.get("type") == "error":
                        raise RuntimeError(f"Claude 流式错误：{ev.get('error')}")
                else:
                    choices = ev.get("choices") or []
                    delta = (choices[0].get("delta") or {}).get("content") if choices else None
                    if delta:
                        yield delta


# ---------------------------------------------------------------- 工具调用循环

_FINAL_NOTICE = "工具调用次数已用完，不要再调用工具，直接基于已有信息给出最终结论。"


def run_tools(
    system: str,
    user: str,
    tools: list[dict[str, Any]],
    call: Callable[[str, dict[str, Any]], str],
    *,
    max_steps: int = 30,
    max_tokens: int = 4096,
    timeout: float = 180,
    wrap_up: tuple[int, str] | None = None,
) -> tuple[str, int]:
    """让模型自主多轮调用工具，直到它给出不带工具调用的最终回答。

    tools 为 [{"name", "description", "parameters": JSON Schema}]，两种协议各自转换；
    call(name, args) 执行工具并返回文本（工具内部异常应自行转成错误文本，循环不兜底）。
    max_steps 是轮数（一轮可并行多个工具调用）；wrap_up=(剩余轮数, 提示) 在只剩这么多轮时
    插入提示（工具仍可用，用于让模型从调研转入执行）；最后一轮之后要求直接作答。
    历史只追加不改写——Claude 的 thinking 块要求原样回传。
    返回 (最终回答, 消耗 token 数)；网络 / HTTP 错误原样抛出。
    """
    cfg = _require()
    notices = {max_steps - 1: _FINAL_NOTICE}
    if wrap_up and 0 < wrap_up[0] < max_steps:
        notices.setdefault(max_steps - 1 - wrap_up[0], wrap_up[1])
    if cfg.kind == "anthropic":
        return _anthropic_tools(cfg, system, user, tools, call, max_steps, max_tokens, timeout, notices)
    return _openai_tools(cfg, system, user, tools, call, max_steps, max_tokens, timeout, notices)


def _openai_tools(cfg, system, user, tools, call, max_steps, max_tokens, timeout, notices) -> tuple[str, int]:
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    specs = [{"type": "function", "function": t} for t in tools]
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    tokens = 0
    text = ""
    for step in range(max_steps + 1):
        body = _merge(
            {"model": cfg.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3,
             "tools": specs, "tool_choice": "auto"},
            cfg.extra,
        )
        r = httpx.post(f"{cfg.base_url}/chat/completions", headers=headers, json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        tokens += int((data.get("usage") or {}).get("total_tokens") or 0)
        msg = data["choices"][0]["message"]
        text = msg.get("content") or text
        calls = msg.get("tool_calls") or []
        if not calls or step == max_steps:
            return text, tokens
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
        for c in calls:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
                result = call(fn.get("name", ""), args if isinstance(args, dict) else {})
            except json.JSONDecodeError:
                result = "错误：参数不是合法 JSON"
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        if step in notices:
            messages.append({"role": "user", "content": notices[step]})
    return text, tokens


def _anthropic_tools(cfg, system, user, tools, call, max_steps, max_tokens, timeout, notices) -> tuple[str, int]:
    specs = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
    tokens = 0
    text = ""
    for step in range(max_steps + 1):
        body = {
            "model": cfg.model,
            "system": system,
            "messages": messages,
            "tools": specs,
            "max_tokens": max_tokens + _ANTHROPIC_THINKING_HEADROOM,
            "output_config": {"effort": "medium"},  # 多步工具决策，比抽取类任务多给一档
        }
        r = httpx.post(**_anthropic_envelope(cfg, body), timeout=max(timeout, 180))
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage") or {}
        tokens += int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        content = data.get("content") or []
        text = _anthropic_text(data) or text
        uses = [b for b in content if b.get("type") == "tool_use"]
        if data.get("stop_reason") != "tool_use" or not uses or step == max_steps:
            return text, tokens
        messages.append({"role": "assistant", "content": content})  # 原样回传（含 thinking 块）
        results: list[dict[str, Any]] = [
            {"type": "tool_result", "tool_use_id": b["id"], "content": call(b["name"], b.get("input") or {})}
            for b in uses
        ]
        if step in notices:
            results.append({"type": "text", "text": notices[step]})
        messages.append({"role": "user", "content": results})
    return text, tokens
