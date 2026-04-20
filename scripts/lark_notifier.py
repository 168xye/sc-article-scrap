"""飞书应用机器人通知

- 通过 /open-apis/im/v1/messages 调用，凭据复用 FEISHU_APP_ID / FEISHU_APP_SECRET。
- 默认用 interactive card（富文本）展示新 GEO 文章列表，并 @ 群里所有人；
  卡片被拒时回退到纯文本。
- 目标会话从 FEISHU_GEO_NOTIFY_RECEIVE_ID / _RECEIVE_ID_TYPE 取，留空则跳过。
"""

from __future__ import annotations

from dataclasses import dataclass

from config import FEISHU_GEO_NOTIFY_RECEIVE_ID, FEISHU_GEO_NOTIFY_RECEIVE_ID_TYPE
from feishu_client import FeishuClient


@dataclass
class GeoNotifyItem:
    source_title: str
    topic_label: str
    geo_title: str
    geo_doc_url: str
    source_url: str


class LarkNotifyError(RuntimeError):
    pass


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
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": "blue",
        },
        "elements": elements,
    }


def _build_text_fallback(items: list[GeoNotifyItem]) -> dict:
    lines = [f'<at user_id="all">所有人</at> 新增 {len(items)} 篇 GEO 文章待审批：', ""]
    for idx, it in enumerate(items, 1):
        lines.append(f"{idx}. {it.geo_title}")
        lines.append(f"   文档: {it.geo_doc_url}")
        lines.append(f"   主题: {it.topic_label}")
        lines.append(f"   原文: {it.source_url}")
    return {"text": "\n".join(lines)}


def send_geo_notification(client: FeishuClient, items: list[GeoNotifyItem]) -> None:
    """向配置的飞书应用机器人目标会话推送 GEO 文章通知。

    未配置 receive_id 或 items 为空时直接返回（不抛异常）。
    发送失败抛 LarkNotifyError，由调用方决定是否吞错。
    """
    if not items:
        return
    receive_id = FEISHU_GEO_NOTIFY_RECEIVE_ID.strip()
    if not receive_id:
        return
    receive_id_type = FEISHU_GEO_NOTIFY_RECEIVE_ID_TYPE or "chat_id"

    card_content = _build_card(items)
    try:
        client.send_im_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="interactive",
            content=card_content,
        )
        return
    except RuntimeError as e:
        card_error = e

    # 卡片被拒时回退到纯文本
    text_content = _build_text_fallback(items)
    try:
        client.send_im_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="text",
            content=text_content,
        )
    except RuntimeError as e:
        raise LarkNotifyError(
            f"飞书通知发送失败（卡片: {card_error}; 文本回退: {e}）"
        ) from e
