"""飞书开放平台 API 客户端：认证、多维表格写入、文档创建"""

import time
import urllib.parse
from typing import Optional

import requests
from config import (
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_BASE_URL,
    FEISHU_BITABLE_APP_TOKEN,
    FEISHU_BITABLE_TABLE_ID,
    FEISHU_FOLDER_TOKEN,
)


class FeishuClient:
    def __init__(self):
        self._token: str = ""
        self._token_expires_at: float = 0
        self._field_name_map: Optional[dict[str, str]] = None

    # ── 认证 ──────────────────────────────────────────────

    def _ensure_token(self) -> None:
        if self._token and time.time() < self._token_expires_at - 300:
            return
        resp = requests.post(
            f"{FEISHU_BASE_URL}/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书认证失败: {data}")
        self._token = data["tenant_access_token"]
        self._token_expires_at = time.time() + data.get("expire", 7200)

    def _headers(self) -> dict:
        self._ensure_token()
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    # ── 多维表格：读取已有记录（用于去重） ──────────────────

    def get_existing_urls(self) -> set[str]:
        """
        从多维表格读取所有已存在的文章 URL，用于去重。
        仅请求「链接」字段，分页遍历全表。
        返回 URL 字符串集合。
        """
        urls = set()
        page_token = None

        while True:
            params: dict = {"page_size": 500}
            # 只取「链接」字段以减少传输量
            params["field_names"] = '["链接"]'
            if page_token:
                params["page_token"] = page_token

            url = (
                f"{FEISHU_BASE_URL}/bitable/v1/apps/"
                f"{FEISHU_BITABLE_APP_TOKEN}/tables/"
                f"{FEISHU_BITABLE_TABLE_ID}/records"
            )
            resp = requests.get(
                url, headers=self._headers(), params=params, timeout=15
            )
            try:
                resp.raise_for_status()
            except requests.HTTPError as e:
                detail = resp.text[:1000]
                raise RuntimeError(
                    f"读取多维表格失败: HTTP {resp.status_code}, response={detail}"
                ) from e
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"读取多维表格失败: {data}")

            items = data.get("data", {}).get("items") or []
            if not isinstance(items, list):
                raise RuntimeError(
                    f"读取多维表格失败: items 不是列表，实际为 {type(items).__name__}"
                )

            for item in items:
                if not isinstance(item, dict):
                    continue
                link_field = item.get("fields", {}).get("链接")
                if isinstance(link_field, dict):
                    link_val = link_field.get("link", "")
                elif isinstance(link_field, str):
                    link_val = link_field
                else:
                    continue
                if link_val:
                    urls.add(link_val)

            if not data.get("data", {}).get("has_more", False):
                break
            page_token = data["data"].get("page_token")

        return urls

    # ── 多维表格：写入记录 ────────────────────────────────

    def _get_bitable_fields_meta(self) -> list[dict]:
        url = (
            f"{FEISHU_BASE_URL}/bitable/v1/apps/"
            f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}/fields"
        )
        resp = requests.get(url, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"读取多维表格字段失败: {data}")
        return data.get("data", {}).get("items", [])

    def _normalize_field_key(self, name: str) -> str:
        return "".join(name.split()).lower()

    def _resolve_field_name_map(self) -> dict[str, str]:
        if self._field_name_map is not None:
            return self._field_name_map

        meta = self._get_bitable_fields_meta()
        actual_names = {item.get("field_name", "") for item in meta}
        normalized_actual = {
            self._normalize_field_key(name): name for name in actual_names if name
        }

        aliases = {
            "标题": ["标题", "title"],
            "链接": ["链接", "url", "原文链接"],
            "主题分类": ["主题分类", "主题", "分类", "topic", "标签"],
            "发布日期": ["发布日期", "发布时间", "日期", "publish date"],
            "摘要": ["摘要", "summary", "简介"],
            "作者": ["作者", "authors", "author"],
            "飞书文档链接": ["飞书文档链接", "文档链接", "飞书文档", "doc", "doc url"],
            "爬取时间": ["爬取时间", "抓取时间", "创建时间", "scraped at"],
        }

        resolved = {}
        missing = []
        for canonical, candidates in aliases.items():
            match = None
            for candidate in candidates:
                if candidate in actual_names:
                    match = candidate
                    break
                normalized = self._normalize_field_key(candidate)
                if normalized in normalized_actual:
                    match = normalized_actual[normalized]
                    break
            if match:
                resolved[canonical] = match
            else:
                missing.append(canonical)

        self._field_name_map = resolved
        if missing:
            raise RuntimeError(
                f"多维表格缺少字段: {', '.join(missing)}，当前字段有: {', '.join(sorted(actual_names))}"
            )
        return resolved

    def add_bitable_record(self, fields: dict) -> str:
        """
        向多维表格添加一条记录，返回 record_id。
        """
        field_name_map = self._resolve_field_name_map()
        mapped_fields = {
            field_name_map.get(key, key): value for key, value in fields.items()
        }

        url = (
            f"{FEISHU_BASE_URL}/bitable/v1/apps/"
            f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}/records"
        )
        resp = requests.post(
            url, headers=self._headers(), json={"fields": mapped_fields}, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"写入多维表格失败: {data}")
        return data["data"]["record"]["record_id"]

    def batch_add_bitable_records(self, records: list[dict]) -> list[str]:
        """批量添加记录（最多 500 条），返回 record_id 列表"""
        url = (
            f"{FEISHU_BASE_URL}/bitable/v1/apps/"
            f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}"
            f"/records/batch_create"
        )
        payload = {"records": [{"fields": r} for r in records]}
        resp = requests.post(
            url, headers=self._headers(), json=payload, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"批量写入多维表格失败: {data}")
        return [r["record_id"] for r in data["data"]["records"]]

    # ── 飞书文档：创建 & 写入 ─────────────────────────────

    def create_document(self, title: str) -> tuple[str, str]:
        """
        在指定文件夹下创建飞书文档。
        返回 (document_id, document_url)。
        """
        url = f"{FEISHU_BASE_URL}/docx/v1/documents"
        body = {"title": title}
        if FEISHU_FOLDER_TOKEN:
            body["folder_token"] = FEISHU_FOLDER_TOKEN
        resp = requests.post(
            url, headers=self._headers(), json=body, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建飞书文档失败: {data}")
        doc = data["data"]["document"]
        doc_id = doc["document_id"]
        doc_url = f"https://feishu.cn/docx/{doc_id}"
        return doc_id, doc_url

    def write_document_content(
        self, document_id: str, blocks: list[dict]
    ) -> None:
        """
        向文档根节点追加内容块。
        blocks 为飞书 Block 结构列表。
        """
        url = (
            f"{FEISHU_BASE_URL}/docx/v1/documents/{document_id}"
            f"/blocks/{document_id}/children"
        )
        batch_size = 50
        for i in range(0, len(blocks), batch_size):
            batch = blocks[i : i + batch_size]
            resp = requests.post(
                url,
                headers=self._headers(),
                json={"children": batch},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"写入文档内容失败: {data}")
            if i + batch_size < len(blocks):
                time.sleep(0.5)

    # ── 辅助：构造文档 Block ──────────────────────────────

    @staticmethod
    def make_heading_block(text: str, level: int = 3) -> dict:
        """构造标题块 (level: 3=H1, 4=H2, 5=H3)"""
        key_map = {3: "heading1", 4: "heading2", 5: "heading3"}
        key = key_map.get(level, "heading2")
        return {
            "block_type": level,
            "children": [],
            key: {
                "elements": [{"text_run": {"content": text}}],
            },
        }

    @staticmethod
    def make_text_block(text: str) -> dict:
        """构造普通文本段落块"""
        # 飞书单个 text_run 限制 2000 字符，超长需拆分
        elements = []
        for i in range(0, len(text), 2000):
            elements.append({"text_run": {"content": text[i : i + 2000]}})
        return {
            "block_type": 2,
            "children": [],
            "text": {"elements": elements},
        }

    @staticmethod
    def make_link_block(text: str, url: str) -> dict:
        """构造含超链接的文本块"""
        encoded_url = urllib.parse.quote(url, safe=":/")
        return {
            "block_type": 2,
            "children": [],
            "text": {
                "elements": [
                    {
                        "text_run": {
                            "content": text,
                            "text_element_style": {
                                "link": {"url": encoded_url},
                                "underline": True,
                            },
                        }
                    }
                ]
            },
        }

    @staticmethod
    def make_divider_block() -> dict:
        """构造分割线块"""
        return {"block_type": 22, "children": []}
