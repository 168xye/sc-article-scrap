"""运行状态持久化：记录最近成功/失败 URL 与失败重试次数。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from config import RUN_STATE_PATH


class RunStateManager:
    def __init__(self, path: str = RUN_STATE_PATH):
        self.path = path
        self.state = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {
                "last_success_at": "",
                "last_failed_url": "",
                "retry_counts": {},
            }
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("run_state 不是 JSON object")
            data.setdefault("last_success_at", "")
            data.setdefault("last_failed_url", "")
            data.setdefault("retry_counts", {})
            return data
        except Exception:
            return {
                "last_success_at": "",
                "last_failed_url": "",
                "retry_counts": {},
            }

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def record_success(self, url: str) -> None:
        self.state["last_success_at"] = self._now_iso()
        self.state["last_failed_url"] = ""
        retries = self.state.get("retry_counts", {})
        if url in retries:
            retries[url] = 0
        self.state["retry_counts"] = retries
        self._save()

    def record_failure(self, url: str) -> int:
        retries = self.state.setdefault("retry_counts", {})
        retries[url] = int(retries.get(url, 0)) + 1
        self.state["last_failed_url"] = url
        self._save()
        return retries[url]

    def get_retry_count(self, url: str) -> int:
        retries = self.state.get("retry_counts", {})
        return int(retries.get(url, 0))
