"""飞书开放平台 API 客户端：认证、多维表格写入、文档创建、应用机器人消息"""

import json
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


_OPTIONAL_FIELD_CANONICALS = {
    "发布日期",
    "作者",
    "GEO文档链接",
    "审批发布状态",
    "关联度",
    "命中关键词",
}


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
            "GEO文档链接": [
                "GEO文档链接", "GEO 文档链接", "geo文档链接",
                "GEO文档", "GEO 文档", "geo doc", "geo doc url",
            ],
            "审批发布状态": [
                "审批发布状态", "审批状态", "发布状态",
                "approval status", "publish status",
            ],
            "关联度": [
                "关联度", "关联度分数", "产品关联度",
                "relevance", "relevance score",
            ],
            "命中关键词": [
                "命中关键词", "关键词命中", "产品关键词",
                "matched keywords", "keywords",
            ],
        }

        resolved = {}
        missing_required = []
        missing_optional = []
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
                if canonical in _OPTIONAL_FIELD_CANONICALS:
                    missing_optional.append(canonical)
                else:
                    missing_required.append(canonical)

        self._field_name_map = resolved
        self._missing_optional_fields = missing_optional
        if missing_required:
            raise RuntimeError(
                f"多维表格缺少字段: {', '.join(missing_required)}，当前字段有: {', '.join(sorted(actual_names))}"
            )
        return resolved

    def get_missing_optional_fields(self) -> list[str]:
        """返回当前 bitable 中缺失的可选字段列表（需先调用过 _resolve_field_name_map）。"""
        self._resolve_field_name_map()
        return list(getattr(self, "_missing_optional_fields", []))

    def _map_fields(self, fields: dict) -> dict:
        """按 canonical→实际字段名映射，自动剔除表中不存在的可选字段。"""
        field_name_map = self._resolve_field_name_map()
        mapped: dict = {}
        for key, value in fields.items():
            actual = field_name_map.get(key)
            if actual is None:
                if key in _OPTIONAL_FIELD_CANONICALS:
                    # 表中未建该列，静默跳过
                    continue
                # 未知字段名按原样传
                actual = key
            mapped[actual] = value
        return mapped

    def add_bitable_record(self, fields: dict) -> str:
        """
        向多维表格添加一条记录，返回 record_id。
        """
        mapped_fields = self._map_fields(fields)

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

    def update_bitable_record(self, record_id: str, fields: dict) -> None:
        """PATCH 更新一条多维表格记录的部分字段。"""
        if not record_id:
            raise ValueError("record_id 不能为空")
        mapped_fields = self._map_fields(fields)
        if not mapped_fields:
            return  # 所有字段都被判定为可选缺失，无需请求

        url = (
            f"{FEISHU_BASE_URL}/bitable/v1/apps/"
            f"{FEISHU_BITABLE_APP_TOKEN}/tables/{FEISHU_BITABLE_TABLE_ID}/records/{record_id}"
        )
        resp = requests.put(
            url, headers=self._headers(), json={"fields": mapped_fields}, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"更新多维表格失败: {data}")

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

    def create_document(
        self,
        title: str,
        folder_token: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        在指定文件夹下创建飞书文档。
        folder_token 为空则使用默认的 FEISHU_FOLDER_TOKEN。
        返回 (document_id, document_url)。
        """
        url = f"{FEISHU_BASE_URL}/docx/v1/documents"
        body = {"title": title}
        target_folder = folder_token if folder_token is not None else FEISHU_FOLDER_TOKEN
        if target_folder:
            body["folder_token"] = target_folder
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
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"写入文档内容失败: HTTP {resp.status_code} "
                    f"(batch {i}-{i + len(batch) - 1} / {len(blocks)}), "
                    f"response={resp.text[:1000]}"
                )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(
                    f"写入文档内容失败 (batch {i}-{i + len(batch) - 1} / {len(blocks)}): {data}"
                )
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
        # 飞书 text_run.content 不允许包含换行（会报 1770001 invalid param），
        # 残留的 \r\n / \n / \r 兜底替换为空格；跨行请在调用方拆成多个 block。
        safe = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        # 飞书单个 text_run 限制 2000 字符，超长需拆分
        elements = []
        for i in range(0, len(safe), 2000):
            elements.append({"text_run": {"content": safe[i : i + 2000]}})
        if not elements:
            elements.append({"text_run": {"content": " "}})
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

    # ── 应用机器人：IM 消息发送 ───────────────────────────

    def send_im_message(
        self,
        *,
        receive_id: str,
        receive_id_type: str,
        msg_type: str,
        content: dict,
    ) -> str:
        """通过应用机器人发送消息，返回 message_id。

        content 传 dict；API 要求最终 payload 里 content 是 JSON 字符串。
        """
        url = (
            f"{FEISHU_BASE_URL}/im/v1/messages"
            f"?receive_id_type={urllib.parse.quote(receive_id_type)}"
        )
        payload = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        }
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=10)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"发送飞书消息失败: HTTP {resp.status_code}, response={resp.text[:500]}"
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise RuntimeError(f"飞书消息响应非 JSON: {resp.text[:500]}") from e
        if data.get("code") != 0:
            raise RuntimeError(f"发送飞书消息失败: {data}")
        return (data.get("data") or {}).get("message_id", "")
