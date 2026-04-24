"""产品关键词与关联度评分

关键词源于 product_info.md（产品功能拆解 + GEO 标签 + 核心价值）。
本模块作为程序可读的关键词清单，product_info.md 仍是人类可读的原始说明。
"""

from __future__ import annotations

PRODUCT_NAME = "Share Creators"

PRODUCT_TAGLINE = (
    "Share Creators 是面向工业设计与汽车研发的 AI 驱动数字资产管理（DAM）平台，"
    "专注于工业设计、游戏和复杂媒体资产管理，提供 AI 搜索、3D 预览与协作能力。"
)

# 关联度评分基线：命中关键词数达到此值即满分 1.0。
# 默认 20，意味着阈值 0.2 对应约 4 个关键词命中，属于合理相关性。
RELEVANCE_MATCH_BASELINE = 20

# 关键词表：分组仅为可读性，计算时整体去重取并集。
_CORE_POSITIONING = [
    "数字资产管理", "DAM", "AI驱动", "AI DAM",
    "工业设计", "汽车研发", "3D数字资产", "3D资产管理", "数字资产",
    "设计资产", "资产管理",
]

_3D_CAD_FORMATS = [
    "3D", "CAD", "3D预览", "CAD在线预览", "在线预览", "3D文件",
    "Alias", "Catia", "SolidWorks", "Rhino",
    "UG", "NX", "Maya", "3DMax", "FBX", "STEP",
]

_AI_CAPABILITIES = [
    "AI搜索", "AI标签", "AI自动标签", "多模态搜索", "语义搜索",
    "自然语言搜索", "自动打标", "资产检索", "AI Agent",
    "AI语义搜索",
]

_VERSION_COLLABORATION = [
    "版本管理", "版本控制", "设计迭代", "版本对比",
    "全球协作", "分布式研发", "跨团队协同", "云端协作", "协作平台",
]

_SECURITY_ENTERPRISE = [
    "企业级", "数据安全", "权限管理", "私有化部署",
    "水印", "操作日志", "区块链", "IP白名单", "VPN",
]

_INTEGRATION = [
    "PLM", "API", "系统集成", "企业系统集成",
    "Jira", "Perforce", "Unity",
]

_STRATEGIC_AI_DATA = [
    "AI数据基础设施", "企业知识库", "设计数据平台", "知识库",
    "AI训练", "设计数据",
]

_AGENT_INSIGHTS = [
    "设计趋势", "竞品分析", "专利情报", "IP情报",
    "设计洞察", "市场洞察", "社媒", "用户偏好",
]

_VALUE_SCENARIOS = [
    "资产复用", "设计评审", "模块化设计", "零部件复用",
    "研发流程", "研发效率", "供应链协作",
]

_INDUSTRY_CONTEXT = [
    "工业", "汽车", "研发", "设计", "协作", "资产",
    "汽车行业", "设计团队",
]


def _build_keyword_list() -> list[str]:
    """合并分组、去重、保持顺序。"""
    seen: set[str] = set()
    out: list[str] = []
    for group in (
        _CORE_POSITIONING,
        _3D_CAD_FORMATS,
        _AI_CAPABILITIES,
        _VERSION_COLLABORATION,
        _SECURITY_ENTERPRISE,
        _INTEGRATION,
        _STRATEGIC_AI_DATA,
        _AGENT_INSIGHTS,
        _VALUE_SCENARIOS,
        _INDUSTRY_CONTEXT,
    ):
        for kw in group:
            key = kw.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(kw)
    return out


PRODUCT_KEYWORDS: list[str] = _build_keyword_list()


def matched_keywords(text: str, keywords: list[str] | None = None) -> list[str]:
    """返回文本中命中的关键词（去重保序，英文不分大小写）。"""
    if not text:
        return []
    kw_list = keywords if keywords is not None else PRODUCT_KEYWORDS
    lower = text.lower()
    seen: set[str] = set()
    out: list[str] = []
    for kw in kw_list:
        if not kw:
            continue
        lower_kw = kw.lower()
        if lower_kw in seen:
            continue
        if lower_kw in lower:
            seen.add(lower_kw)
            out.append(kw)
    return out


def compute_relevance(
    text: str,
    keywords: list[str] | None = None,
    baseline: int = RELEVANCE_MATCH_BASELINE,
) -> float:
    """计算文本 × 产品关键词的关联度，范围 [0, 1]。

    分数 = min(1.0, 命中关键词数 / baseline)。
    baseline 默认 20，意味着命中 20 个关键词即满分；阈值 0.2 ≈ 命中 4 个。
    """
    hits = matched_keywords(text, keywords=keywords)
    denom = max(1, baseline)
    return min(1.0, len(hits) / denom)
