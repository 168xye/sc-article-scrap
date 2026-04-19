"""Lark（飞书）自定义机器人通知

- 通过 Webhook 推送消息；若机器人启用「加签」验证，自动计算签名。
- 默认用 interactive card（富文本）展示新 GEO 文章列表，并 @ 群里所有人。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Optional

import requests

from config import LARK_GEO_BOT_SECRET, LARK_GEO_BOT_WEBHOOK


@dataclass
class GeoNotifyItem:
    source_title: str
    topic_label: str
    geo_title: str
    geo_doc_url: str
    source_url: str


class LarkNotifyError(RuntimeError):
    pass


def _sign(timestamp: int, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _build_card(items: list[GeoNotifyItem]) -> dict:
    header_title = f"新增 {len(items)} 篇 GEO 文章待审批"
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": "<at id=all></at> 以下 GEO 文章已生成并入库，审批状态为 **待审批**，请相关同学查阅。",
        },
        {"tag": "hr"},
    ]

    for idx, it in enumerate(items, 1):
        lines = [
            f"**{idx}. [{it.geo_title}]({it.geo_doc_url})**",
            f"主题：{it.topic_label}",
            f"原文：[{it.source_title}]({it.source_url})",
        ]
        elements.append({"tag": "markdown", "content": "\n".join(lines)})
        if idx != len(items):
            elements.append({"tag": "hr"})

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": header_title},
                "template": "blue",
            },
            "elements": elements,
        },
    }


def _build_text_fallback(items: list[GeoNotifyItem]) -> dict:
    lines = [f'<at user_id="all">所有人</at> 新增 {len(items)} 篇 GEO 文章待审批：', ""]
    for idx, it in enumerate(items, 1):
        lines.append(f"{idx}. {it.geo_title}")
        lines.append(f"   文档: {it.geo_doc_url}")
        lines.append(f"   主题: {it.topic_label}")
        lines.append(f"   原文: {it.source_url}")
    return {"msg_type": "text", "content": {"text": "\n".join(lines)}}


def send_geo_notification(items: list[GeoNotifyItem]) -> None:
    """向配置的飞书自定义机器人发送 GEO 文章通知。

    未配置 webhook 或 items 为空时直接返回（不抛异常）。
    发送失败抛 LarkNotifyError，由调用方决定是否吞错。
    """
    if not items:
        return
    webhook = LARK_GEO_BOT_WEBHOOK.strip()
    if not webhook:
        return

    payload = _build_card(items)
    if LARK_GEO_BOT_SECRET:
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = _sign(ts, LARK_GEO_BOT_SECRET)

    try:
        resp = requests.post(webhook, json=payload, timeout=10)
    except requests.RequestException as e:
        raise LarkNotifyError(f"发送飞书通知失败: {e}") from e

    if resp.status_code >= 400:
        raise LarkNotifyError(
            f"飞书通知 HTTP {resp.status_code}: {resp.text[:500]}"
        )

    try:
        data = resp.json()
    except ValueError:
        data = {}

    code = data.get("code") if isinstance(data, dict) else None
    # 自定义机器人成功返回 {"StatusCode":0,...} 或 {"code":0,...}，
    # 失败返回 code 非 0 的错误信息。
    status_code_field = data.get("StatusCode") if isinstance(data, dict) else None
    if code not in (0, None) or (status_code_field is not None and status_code_field != 0):
        # 卡片格式被拒时，回退到纯文本
        fallback = _build_text_fallback(items)
        if LARK_GEO_BOT_SECRET:
            ts = int(time.time())
            fallback["timestamp"] = str(ts)
            fallback["sign"] = _sign(ts, LARK_GEO_BOT_SECRET)
        try:
            resp2 = requests.post(webhook, json=fallback, timeout=10)
        except requests.RequestException as e:
            raise LarkNotifyError(f"发送飞书通知失败（回退文本）: {e}") from e
        if resp2.status_code >= 400:
            raise LarkNotifyError(
                f"飞书通知（回退文本）HTTP {resp2.status_code}: {resp2.text[:500]}"
            )
