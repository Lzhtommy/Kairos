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
    elif "open.feishu.cn" in url:  # 飞书自定义机器人
        payload = {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
    elif "sctapi.ftqq.com" in url:  # Server 酱
        payload = {"title": title, "desp": text}
    else:
        payload = {"title": title, "text": text}
    r = httpx.post(url, json=payload, timeout=15)
    r.raise_for_status()
    return True
