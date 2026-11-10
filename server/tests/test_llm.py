"""LLM 调用层：provider 解析、请求体拼装、响应解析。不碰网络。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.core.config import settings
from app.services import llm


@pytest.fixture(autouse=True)
def clean_llm_settings(monkeypatch):
    for key in ("llm_api_key", "llm_base_url", "llm_model", "llm_extra_body", "deepseek_api_key"):
        monkeypatch.setattr(settings, key, "")
    monkeypatch.setattr(settings, "deepseek_model", "deepseek-flash")
    monkeypatch.setattr(settings, "deepseek_base_url", "https://api.deepseek.com")


class Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def capture_post(monkeypatch, body):
    calls: list[dict] = []

    def fake_post(url, headers, json, timeout):  # noqa: A002
        calls.append({"url": url, "headers": headers, "json": json})
        return Resp(body)

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_disabled_without_key_or_for_rule(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "auto")
    assert not llm.enabled()
    monkeypatch.setattr(settings, "deepseek_api_key", "k")
    assert llm.enabled() and llm.model_name() == "deepseek-flash"
    monkeypatch.setattr(settings, "ai_provider", "rule")
    assert not llm.enabled()


def test_legacy_deepseek_request(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "auto")
    monkeypatch.setattr(settings, "deepseek_api_key", "k")
    calls = capture_post(monkeypatch, {"choices": [{"message": {"content": '{"a": 1}'}}], "usage": {"total_tokens": 9}})
    assert llm.chat_json([{"role": "user", "content": "JSON"}]) == ({"a": 1}, 9)
    req = calls[0]
    assert req["url"] == "https://api.deepseek.com/chat/completions"
    assert req["headers"]["Authorization"] == "Bearer k"
    assert req["json"]["thinking"] == {"type": "disabled"}
    assert req["json"]["response_format"] == {"type": "json_object"}


def test_openai_compatible_needs_model_and_extra_body_overrides(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    assert not llm.enabled()  # openai 没有默认模型
    monkeypatch.setattr(settings, "llm_model", "qwen-plus")
    monkeypatch.setattr(settings, "llm_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1/")
    monkeypatch.setattr(settings, "llm_extra_body", '{"temperature": null, "enable_thinking": false}')
    calls = capture_post(monkeypatch, {"choices": [{"message": {"content": "```json\n{\"a\": 1}\n```"}}]})
    assert llm.chat_json([{"role": "user", "content": "x"}]) == ({"a": 1}, 0)
    body = calls[0]["json"]
    assert calls[0]["url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert body["model"] == "qwen-plus" and "temperature" not in body and body["enable_thinking"] is False
    assert "thinking" not in body  # DeepSeek 专有参数不外溢


def test_keyless_local_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "openai")
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "llm_model", "qwen3")
    calls = capture_post(monkeypatch, {"choices": [{"message": {"content": "{}"}}]})
    llm.chat_json([{"role": "user", "content": "x"}])
    assert "Authorization" not in calls[0]["headers"]


def test_anthropic_request_shape(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "sk-ant")
    calls = capture_post(monkeypatch, {
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": 'ok {"a": 2} done'}],
        "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 4},
    })
    out = llm.chat_json([
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "stray"},
        {"role": "user", "content": "u1"},
        {"role": "user", "content": "u2"},
    ], max_tokens=100)
    assert out == ({"a": 2}, 7)
    req = calls[0]
    assert req["url"] == "https://api.anthropic.com/v1/messages"
    assert req["headers"]["x-api-key"] == "sk-ant"
    body = req["json"]
    assert body["model"] == "claude-opus-5-5"
    assert body["system"].startswith("sys")
    assert body["messages"] == [{"role": "user", "content": "u1\n\nu2"}]  # 去掉开头 assistant、合并相邻 user
    assert "temperature" not in body and body["max_tokens"] > 100
    assert body["fallbacks"] == "default"


def test_anthropic_proxy_skips_fallback_beta(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    monkeypatch.setattr(settings, "llm_base_url", "https://proxy.example.com")
    calls = capture_post(monkeypatch, {"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn"})
    llm.chat_json([{"role": "user", "content": "x"}])
    assert "fallbacks" not in calls[0]["json"] and "anthropic-beta" not in calls[0]["headers"]


def test_anthropic_refusal_raises(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    capture_post(monkeypatch, {"content": [], "stop_reason": "refusal"})
    with pytest.raises(RuntimeError):
        llm.chat_json([{"role": "user", "content": "x"}])


def _sse_transport(lines: list[str]):
    def handler(request):
        return httpx.Response(200, content="\n".join(lines).encode())

    return httpx.MockTransport(handler)


def _collect_stream(monkeypatch, lines):
    transport = _sse_transport(lines)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    async def run():
        return [d async for d in llm.stream_text([{"role": "user", "content": "x"}])]

    return asyncio.run(run())


def test_stream_openai(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "deepseek")
    monkeypatch.setattr(settings, "deepseek_api_key", "k")
    chunk = lambda t: "data: " + json.dumps({"choices": [{"delta": {"content": t}}]})  # noqa: E731
    lines = [chunk("你"), ": keep-alive", chunk("好"), "data: " + json.dumps({"choices": []}), "data: [DONE]"]
    assert _collect_stream(monkeypatch, lines) == ["你", "好"]


def test_stream_anthropic(monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "k")
    ev = lambda d: "data: " + json.dumps(d)  # noqa: E731
    lines = [
        "event: message_start", ev({"type": "message_start", "message": {}}),
        ev({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "..."}}),
        ev({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "A"}}),
        ev({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "B"}}),
        ev({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
        ev({"type": "message_stop"}),
    ]
    assert _collect_stream(monkeypatch, lines) == ["A", "B"]
