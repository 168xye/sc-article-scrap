"""登录态管理：仅检查现有 storage_state 是否可用。"""

from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

from config import PLAYWRIGHT_STORAGE_STATE_PATH


def _now_ts() -> float:
    return time.time()


def _load_state(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _cookie_expiry_ts(state: dict) -> Optional[float]:
    expiries: list[float] = []
    for cookie in state.get("cookies", []):
        domain = (cookie.get("domain") or "").lower()
        if "mckinsey.com" not in domain:
            continue
        expires = cookie.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            expiries.append(float(expires))
    if not expiries:
        return None
    return min(expiries)


def auth_state_status(path: str = PLAYWRIGHT_STORAGE_STATE_PATH) -> tuple[bool, str]:
    """返回 (是否可用, 描述信息)。"""
    if not os.path.exists(path):
        return False, "登录状态文件不存在"

    try:
        state = _load_state(path)
    except Exception as e:
        return False, f"登录状态文件无法读取: {e}"

    expiry = _cookie_expiry_ts(state)
    if expiry is None:
        return False, "未找到可用的 mckinsey cookie 过期时间"

    remain_hours = (expiry - _now_ts()) / 3600
    if remain_hours <= 0:
        return False, "登录状态已过期"

    return True, f"登录状态有效，剩余约 {remain_hours:.1f} 小时"


def ensure_auth_state(
    *,
    auto_refresh: bool = False,
    emit: Optional[Callable[[str, str], None]] = None,
    force_refresh: bool = False,
) -> bool:
    """仅检查当前登录状态是否可用。返回 True 表示可继续抓取。"""

    def _say(tag: str, message: str) -> None:
        if emit:
            emit(tag, message)

    ok, reason = auth_state_status(path=PLAYWRIGHT_STORAGE_STATE_PATH)
    if ok:
        _say("PROGRESS", f"认证检查: {reason}")
        return True

    if force_refresh or auto_refresh:
        _say("PROGRESS", "后台执行模式已禁用自动刷新登录态，仅使用现有登录状态")
    _say("FAIL", f"认证检查失败: {reason}")
    return False
