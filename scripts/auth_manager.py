"""登录态管理：检查 storage_state 是否即将过期，必要时触发刷新。"""

from __future__ import annotations

import json
import os
import time
from typing import Callable

from config import (
    AUTH_REFRESH_THRESHOLD_HOURS,
    PLAYWRIGHT_STORAGE_STATE_PATH,
)
from login_helper import save_login_state_interactive


def _now_ts() -> float:
    return time.time()


def _load_state(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _cookie_expiry_ts(state: dict) -> float | None:
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


def should_refresh_auth_state(
    *,
    threshold_hours: int = AUTH_REFRESH_THRESHOLD_HOURS,
    path: str = PLAYWRIGHT_STORAGE_STATE_PATH,
) -> tuple[bool, str]:
    ok, msg = auth_state_status(path=path)
    if not ok:
        return True, msg

    state = _load_state(path)
    expiry = _cookie_expiry_ts(state)
    if expiry is None:
        return True, "登录状态缺失有效过期时间"

    remain_hours = (expiry - _now_ts()) / 3600
    if remain_hours <= threshold_hours:
        return True, f"登录状态将在 {remain_hours:.1f} 小时内过期"
    return False, f"登录状态稳定（剩余 {remain_hours:.1f} 小时）"


def ensure_auth_state(
    *,
    auto_refresh: bool = True,
    threshold_hours: int = AUTH_REFRESH_THRESHOLD_HOURS,
    emit: Callable[[str, str], None] | None = None,
    force_refresh: bool = False,
) -> bool:
    """确保登录状态可用。返回 True 表示可继续抓取。"""

    def _say(tag: str, message: str) -> None:
        if emit:
            emit(tag, message)

    need_refresh, reason = should_refresh_auth_state(
        threshold_hours=threshold_hours,
        path=PLAYWRIGHT_STORAGE_STATE_PATH,
    )

    if force_refresh:
        need_refresh = True
        reason = "收到强制刷新请求"

    if not need_refresh:
        _say("PROGRESS", f"认证检查: {reason}")
        return True

    if not auto_refresh:
        _say("FAIL", f"认证检查失败且已禁用自动刷新: {reason}")
        return False

    _say("PROGRESS", f"认证检查: {reason}，启动登录态刷新流程")
    ok = save_login_state_interactive(emit=emit)
    if ok:
        _say("OK", "登录态刷新完成，继续抓取")
        return True

    _say("FAIL", "登录态刷新失败，可能需要人工完成验证码/二次验证")
    return False
