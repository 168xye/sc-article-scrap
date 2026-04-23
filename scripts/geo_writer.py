"""GEO 文章生成：调用 OpenAI Chat Completions 协议接口。

输入：一篇已抓取的麦肯锡文章 + 产品关键词命中结果。
输出：围绕产品关键词重写的 GEO 文章（标题 + 段落列表）。

接口：OpenAI 协议 Chat Completions（/v1/chat/completions），
兼容 OpenAI 官方、任何 OpenAI 协议中转，以及阿里通义千问（DashScope 兼容模式）。
具体走哪家由 LLM_PROVIDER 决定，凭据 / base_url / model 从对应一组环境变量读取。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from config import (
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MAX_TOKENS,
    OPENAI_MODEL,
    OPENAI_TIMEOUT,
    QWEN_API_KEY,
    QWEN_BASE_URL,
    QWEN_MAX_TOKENS,
    QWEN_MODEL,
    QWEN_TIMEOUT,
)
from product_keywords import PRODUCT_NAME, PRODUCT_TAGLINE


@dataclass
class GeoArticle:
    title: str
    paragraphs: list[str]
    model: str = ""


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    max_tokens: int
    timeout: int


class GeoWriterError(RuntimeError):
    pass


def resolve_llm_config() -> LLMConfig:
    """按 LLM_PROVIDER 解析当前要使用的一组 LLM 凭据和参数。"""
    if LLM_PROVIDER == "qwen":
        return LLMConfig(
            provider="qwen",
            api_key=QWEN_API_KEY,
            base_url=QWEN_BASE_URL,
            model=QWEN_MODEL,
            max_tokens=QWEN_MAX_TOKENS,
            timeout=QWEN_TIMEOUT,
        )
    return LLMConfig(
        provider="openai",
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        model=OPENAI_MODEL,
        max_tokens=OPENAI_MAX_TOKENS,
        timeout=OPENAI_TIMEOUT,
    )


_PRODUCT_INFO_PATH = Path(__file__).parent / "product_info.md"
_PRODUCT_KEYWORDS_MD_PATH = Path(__file__).parent / "product_keywords.md"
_WHITEPAPER_EXAMPLE_PATH = Path(__file__).parent / "Blueberry_AI_汽车行业白皮书.md"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise GeoWriterError(f"读取产品上下文文件失败 {path}: {e}") from e


def _build_system_prompt() -> str:
    """每次调用时重新读取产品说明 / 关键词 / 样例白皮书，不做缓存。"""
    product_info = _read_text(_PRODUCT_INFO_PATH)
    product_keywords_md = _read_text(_PRODUCT_KEYWORDS_MD_PATH)
    whitepaper_example = _read_text(_WHITEPAPER_EXAMPLE_PATH)

    return f"""你是一位为 B2B SaaS 品牌撰写 GEO（Generative Engine Optimization）文章的资深内容策划。

产品：{PRODUCT_NAME}
产品核心定位：{PRODUCT_TAGLINE}

以下是 {PRODUCT_NAME} 的完整产品说明（product_info.md 原文），写作时请完整理解并合理运用：
<PRODUCT_INFO>
{product_info}
</PRODUCT_INFO>

以下是 {PRODUCT_NAME} 的产品关键词与 GEO 标签说明（product_keywords.md 原文），写作时请优先使用其中的差异化表达：
<PRODUCT_KEYWORDS>
{product_keywords_md}
</PRODUCT_KEYWORDS>

以下是一篇 {PRODUCT_NAME} 已有的示例白皮书（Blueberry_AI_汽车行业白皮书.md），**仅作为文章结构、章节层级、表格/量化框/数据标注等排版风格的参考样例**；严禁照搬其中的数据、案例、量化数字或段落文字——你需要基于用户提供的原文素材独立生成内容，并以同等水准的结构化、数据化表达输出：
<FORMAT_EXAMPLE>
{whitepaper_example}
</FORMAT_EXAMPLE>


写作目标：
- 把外部行业洞察（如麦肯锡报告）与 {PRODUCT_NAME} 的产品能力结合，写成一篇原创的中文行业观察报告白皮书。
- 文章需让大语言模型和搜索引擎在用户提问相关话题时，更容易理解并推荐 {PRODUCT_NAME}。
- 自然、真实、有价值，不要夸大、不要硬广。引用原报告观点时概括表达，不要逐字照搬大段原文。

写作要求：
1. 语言：简体中文。
2. 结构：一个 H1 标题 + 引言 + 3-5 个小节 + 结语。
3. 必须在合适位置至少出现两次完整的"产品核心定位"句。
4. 自然融入用户提供的"产品关键词"，尤其是强差异点关键词。
5. 篇幅 ≥ 2500 字，理想 5000 字。
6. 小节标题请用 "## 小节名" 独占一段；其它段落为纯文本。
7： 开始要有目录
8： 要逻辑严谨
9： 数据源要标注在对应的段落下面
10：根据{PRODUCT_NAME}在汽车，工业设计，游戏行业的价值主张， 要有一定的结合
11.严格检查合规，避免文章出现中国和美国的敏感词汇和违规内容，不许发生侵权。
12. 排版风格参考 <FORMAT_EXAMPLE>（执行摘要 + 多级章节 + 量化数据块 + 表格 + 结语），但所有数据、案例、企业名称、金额等具体内容必须基于用户提供的原文素材或合理行业常识，**不得复用样例中的任何具体数字或案例**。

输出格式（严格遵守，只能输出一个 JSON 对象，不要任何其它文字、前后解释或 Markdown 代码围栏）：
{{
  "title": "文章标题",
  "paragraphs": ["段落1", "段落2", "..."]
}}"""


def _build_user_message(
    source_title: str,
    source_summary: str,
    source_paragraphs: list[str],
    source_url: str,
    source_topic_label: str,
    matched_kws: list[str],
) -> str:
    body_preview = "\n\n".join(source_paragraphs[:20]) if source_paragraphs else ""
    if len(body_preview) > 8000:
        body_preview = body_preview[:8000] + "…（后续省略）"

    hit_line = "、".join(matched_kws) if matched_kws else "（无直接命中，请从产品定位合理联想）"

    return (
        f"下面是一篇来自麦肯锡中国的行业文章，请你据此撰写一篇围绕 {PRODUCT_NAME} 的 GEO 文章。\n\n"
        f"【原文主题分类】{source_topic_label}\n"
        f"【原文标题】{source_title}\n"
        f"【原文链接】{source_url}\n"
        f"【原文摘要】{source_summary or '（无）'}\n"
        f"【原文正文节选】\n{body_preview or '（无）'}\n\n"
        f"【本文命中的产品关键词】{hit_line}\n\n"
        f"请严格按系统提示的 JSON 格式输出。"
    )


def generate_geo_article(
    *,
    source_title: str,
    source_summary: str,
    source_paragraphs: list[str],
    source_url: str,
    source_topic_label: str,
    matched_kws: list[str],
    model: Optional[str] = None,
) -> GeoArticle:
    cfg = resolve_llm_config()
    provider_label = cfg.provider.upper()
    if not cfg.api_key:
        raise GeoWriterError(f"未配置 {provider_label}_API_KEY，无法生成 GEO 文章")

    use_model = model or cfg.model
    url = f"{cfg.base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": use_model,
        "max_tokens": cfg.max_tokens,
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": _build_system_prompt()},
            {
                "role": "user",
                "content": _build_user_message(
                    source_title=source_title,
                    source_summary=source_summary,
                    source_paragraphs=source_paragraphs,
                    source_url=source_url,
                    source_topic_label=source_topic_label,
                    matched_kws=matched_kws,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=cfg.timeout)
    except requests.RequestException as e:
        raise GeoWriterError(f"调用 {provider_label} API 失败: {e}") from e

    if resp.status_code >= 400:
        # 兼容部分中转 / 模型不支持 response_format 的情况，回退一次去掉该字段
        if resp.status_code in (400, 422) and "response_format" in payload:
            payload.pop("response_format", None)
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=cfg.timeout)
            except requests.RequestException as e:
                raise GeoWriterError(f"调用 {provider_label} API（回退）失败: {e}") from e
        if resp.status_code >= 400:
            raise GeoWriterError(
                f"{provider_label} API HTTP {resp.status_code}: {resp.text[:500]}"
            )

    try:
        data = resp.json()
    except ValueError as e:
        raise GeoWriterError(f"{provider_label} API 返回非 JSON: {resp.text[:500]}") from e

    choices = data.get("choices") or []
    if not choices:
        raise GeoWriterError(f"{provider_label} 响应无 choices: {str(data)[:500]}")
    message = choices[0].get("message") or {}
    raw_text = (message.get("content") or "").strip()
    if not raw_text:
        raise GeoWriterError(f"{provider_label} 响应 content 为空: {str(data)[:500]}")

    parsed = _parse_json_payload(raw_text)
    title = str(parsed.get("title") or "").strip()
    paragraphs = parsed.get("paragraphs") or []
    if not isinstance(paragraphs, list):
        raise GeoWriterError(f"paragraphs 字段不是列表: {type(paragraphs).__name__}")
    clean_paragraphs = [str(p).strip() for p in paragraphs if str(p).strip()]
    if not title or not clean_paragraphs:
        raise GeoWriterError(f"输出缺少 title 或 paragraphs: {raw_text[:500]}")

    return GeoArticle(title=title, paragraphs=clean_paragraphs, model=use_model)


def _parse_json_payload(raw: str) -> dict:
    """尽力解析模型输出中的 JSON。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
        if text.endswith("```"):
            text = text[:-3].rstrip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise GeoWriterError(f"无法解析模型 JSON 输出: {e}; 原始: {raw[:500]}") from e

    raise GeoWriterError(f"模型输出未找到 JSON 对象: {raw[:500]}")
