"""通知发送：邮件（SMTP SSL）与通用 webhook。

webhook 按 URL 自动适配企业微信 / 飞书机器人的消息格式，其余按
{"title", "text"} 的通用 JSON POST。所有失败只记日志不抛——通知永远
不能反过来弄挂业务流程。
"""

from __future__ import annotations

import logging
import smtplib
from email.header import Header
from email.mime.text import MIMEText

import httpx

from app.core.config import settings

logger = logging.getLogger("kairos.notify")


def send(channel: str, target: str, title: str, text: str) -> bool:
    """按渠道派发；返回是否成功（供"发送测试通知"回显）。"""
    try:
        if channel == "email" and target:
            return _send_email(target, title, text)
        if channel == "webhook" and target:
            return _send_webhook(target, title, text)
    except Exception:  # noqa: BLE001
        logger.exception("notify via %s failed", channel)
    return False


def _send_email(to: str, title: str, text: str) -> bool:
    if not settings.smtp_host:
        logger.warning("SMTP 未配置（smtp_host 为空），邮件通知跳过")
        return False
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    # 云厂商普遍封 25 端口，默认走 465 SSL
    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as srv:
        if settings.smtp_user:
            srv.login(settings.smtp_user, settings.smtp_password)
        srv.sendmail(msg["From"], [to], msg.as_string())
    return True


def _send_webhook(url: str, title: str, text: str) -> bool:
    if "qyapi.weixin.qq.com" in url:  # 企业微信群机器人
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    elif "open.feishu.cn" in url or "open.larksuite.com" in url:  # 飞书自定义机器人
        payload = {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
    else:
        payload = {"title": title, "text": text}
    r = httpx.post(url, json=payload, timeout=15)
    r.raise_for_status()
    # 飞书 / 企业微信出错（签名校验、关键词不匹配、地址失效）也回 HTTP 200，错误码在响应体里
    try:
        body = r.json()
    except ValueError:
        return True
    code = body.get("code", body.get("errcode", 0)) if isinstance(body, dict) else 0
    if code:
        logger.warning("webhook rejected: %s", str(body)[:200])
        return False
    return True
