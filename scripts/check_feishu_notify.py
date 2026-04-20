#!/usr/bin/env python3
"""飞书应用机器人通知自检

用途：独立排查 send_im_message 的 230001 invalid receive_id 类错误。
    1. 用 FEISHU_APP_ID / FEISHU_APP_SECRET 拉 tenant_access_token
    2. 调用 /open-apis/im/v1/chats 列出机器人所在所有群及其真实 chat_id
    3. 和 .env 里 FEISHU_GEO_NOTIFY_RECEIVE_ID 比对
    4. 尝试向目标发一条文本测试消息

运行:
    cd scripts
    python3 check_feishu_notify.py
    # 可选：只想列群不发消息
    python3 check_feishu_notify.py --list-only
    # 可选：临时覆盖目标
    python3 check_feishu_notify.py --receive-id oc_xxx --receive-id-type chat_id
"""

from __future__ import annotations

import argparse
import sys

import requests

from config import (
    FEISHU_APP_ID,
    FEISHU_BASE_URL,
    FEISHU_GEO_NOTIFY_RECEIVE_ID,
    FEISHU_GEO_NOTIFY_RECEIVE_ID_TYPE,
)
from feishu_client import FeishuClient


def list_bot_chats(client: FeishuClient) -> list[dict]:
    """列机器人作为成员所在的全部群（需要 im:chat:readonly 或 im:chat scope）。"""
    url = f"{FEISHU_BASE_URL}/im/v1/chats"
    page_token = None
    chats: list[dict] = []
    while True:
        params: dict = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=client._headers(), params=params, timeout=10)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"列群失败: HTTP {resp.status_code}, response={resp.text[:500]}"
            )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"列群失败: {data}")
        items = data.get("data", {}).get("items") or []
        chats.extend(items)
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data["data"].get("page_token")
    return chats


def mask(s: str, keep: int = 6) -> str:
    if not s:
        return "(空)"
    if len(s) <= keep * 2:
        return s[:keep] + "…"
    return f"{s[:keep]}…{s[-keep:]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--receive-id", default=None, help="覆盖 env 里的 receive_id")
    ap.add_argument(
        "--receive-id-type", default=None,
        help="覆盖 env 里的 receive_id_type（chat_id/open_id/user_id/union_id/email）",
    )
    ap.add_argument("--list-only", action="store_true", help="只列群不发消息")
    ap.add_argument(
        "--msg", default="【自检】飞书应用机器人消息链路正常",
        help="测试消息内容",
    )
    args = ap.parse_args()

    target_id = (args.receive_id or FEISHU_GEO_NOTIFY_RECEIVE_ID or "").strip()
    target_type = (args.receive_id_type or FEISHU_GEO_NOTIFY_RECEIVE_ID_TYPE or "chat_id").strip().lower()

    print("=== 飞书应用机器人自检 ===")
    print(f"APP_ID          : {mask(FEISHU_APP_ID)}")
    print(f"RECEIVE_ID      : {target_id or '(未配置)'}")
    print(f"RECEIVE_ID_TYPE : {target_type}")
    print()

    client = FeishuClient()

    # Step 1: 拉 tenant_access_token
    try:
        client._ensure_token()
        print("[OK] 获取 tenant_access_token 成功（app_id / app_secret 有效）")
    except Exception as e:
        print(f"[FAIL] 获取 token 失败: {e}")
        print("       → 检查 FEISHU_APP_ID / FEISHU_APP_SECRET 是否正确、是否启用")
        return 1

    # Step 2: 列机器人所在所有群
    print()
    print("=== 机器人所在群列表（API 真实 chat_id） ===")
    try:
        chats = list_bot_chats(client)
    except Exception as e:
        chats = []
        print(f"[WARN] 列群失败: {e}")
        print("       → 可能缺少 im:chat:readonly（或 im:chat）scope；仍可继续发送测试")

    if chats:
        for c in chats:
            print(
                f"  chat_id={c.get('chat_id')}  "
                f"name={c.get('name')!r}  "
                f"mode={c.get('chat_mode')}  "
                f"tenant_key={mask(c.get('tenant_key') or '', 4)}"
            )
    else:
        print("  (无 —— 机器人未加入任何群，或无列群权限)")

    # Step 3: 比对
    print()
    print("=== 目标比对 ===")
    if not target_id:
        print("[SKIP] 未配置 receive_id，无法比对")
    elif target_type == "chat_id" and chats:
        hit = next((c for c in chats if c.get("chat_id") == target_id), None)
        if hit:
            print(f"[OK] 目标 chat_id 在机器人所在群列表中：{hit.get('name')!r}")
        else:
            print(f"[WARN] 目标 chat_id 不在机器人所在群列表中：{target_id}")
            print("       常见原因：")
            print("         1) 机器人没被加进这个群 → 到群设置里添加本应用机器人")
            print("         2) chat_id 抄错/残缺 → 用上方列出的真实 chat_id 替换 .env")
            print("         3) chat_id 属于另一个租户的群 → 用本租户的群")
    else:
        print("[INFO] receive_id_type 不是 chat_id 或群列表为空，跳过自动比对")

    if args.list_only:
        return 0

    # Step 4: 尝试发送测试消息
    print()
    print("=== 发送测试消息 ===")
    if not target_id:
        print("[SKIP] 未配置 receive_id")
        return 0

    try:
        message_id = client.send_im_message(
            receive_id=target_id,
            receive_id_type=target_type,
            msg_type="text",
            content={"text": args.msg},
        )
        print(f"[OK] 发送成功 message_id={message_id}")
        return 0
    except Exception as e:
        print(f"[FAIL] {e}")
        print()
        print("排查建议：")
        print("  - 230001 invalid receive_id → 上面「目标比对」里看提示")
        print("  - 99991663 / 99991668 → 应用缺 im:message:send_as_bot scope")
        print("  - 230002 bot_not_in_chat → 机器人未加入该会话")
        return 1


if __name__ == "__main__":
    sys.exit(main())
