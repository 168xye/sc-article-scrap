"""飞书 API 和麦肯锡中国爬取配置"""

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ── 飞书配置 ──────────────────────────────────────────────
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_BITABLE_APP_TOKEN = os.getenv("FEISHU_BITABLE_APP_TOKEN", "")
FEISHU_BITABLE_TABLE_ID = os.getenv("FEISHU_BITABLE_TABLE_ID", "")
FEISHU_FOLDER_TOKEN = os.getenv("FEISHU_FOLDER_TOKEN", "")
# 生成的 GEO 文章存放文件夹；留空则与 FEISHU_FOLDER_TOKEN 共用。
FEISHU_GEO_FOLDER_TOKEN = os.getenv("FEISHU_GEO_FOLDER_TOKEN", "")

FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"

# ── 飞书 GEO 应用机器人通知 ──────────────────────────────
# 使用顶部 FEISHU_APP_ID / FEISHU_APP_SECRET 申请 tenant_access_token，
# 调用 /open-apis/im/v1/messages 发送消息（需要应用开通 im:message:send_as_bot scope，
# 并把机器人加入目标群）。留空则跳过通知。
FEISHU_GEO_NOTIFY_RECEIVE_ID = os.getenv("FEISHU_GEO_NOTIFY_RECEIVE_ID", "")
FEISHU_GEO_NOTIFY_RECEIVE_ID_TYPE = os.getenv(
    "FEISHU_GEO_NOTIFY_RECEIVE_ID_TYPE", "chat_id"
).strip().lower()

# ── 关联度阈值 ────────────────────────────────────────────
# 文章与产品关键词的关联度分数（0-1）。低于此值则不入库、不生成 GEO 文章。
try:
    RELEVANCE_THRESHOLD = float(os.getenv("RELEVANCE_THRESHOLD", "0.2"))
except ValueError:
    RELEVANCE_THRESHOLD = 0.2

# ── LLM 供应商选择 ───────────────────────────────────────
# GEO 文章生成走哪家模型：openai | qwen
# 两家都基于 OpenAI Chat Completions 协议，仅凭据 / base_url / model 不同。
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()

# ── OpenAI（GEO 生成） ───────────────────────────────────
# 走 Chat Completions 协议；可通过 OPENAI_BASE_URL 切到 OpenAI 协议中转。
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-codex")
try:
    OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "4096"))
except ValueError:
    OPENAI_MAX_TOKENS = 4096
try:
    OPENAI_TIMEOUT = int(os.getenv("OPENAI_TIMEOUT", "120"))
except ValueError:
    OPENAI_TIMEOUT = 120

# ── 通义千问（DashScope 兼容模式） ───────────────────────
# DashScope 提供 OpenAI 协议兼容端点，base_url 保持到 /compatible-mode 即可，
# geo_writer 会拼接 /v1/chat/completions。
# API KEY 获取：https://dashscope.console.aliyun.com/apiKey
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3-max")
try:
    QWEN_MAX_TOKENS = int(os.getenv("QWEN_MAX_TOKENS", "4096"))
except ValueError:
    QWEN_MAX_TOKENS = 4096
try:
    QWEN_TIMEOUT = int(os.getenv("QWEN_TIMEOUT", "120"))
except ValueError:
    QWEN_TIMEOUT = 120

# ── 代理 ──────────────────────────────────────────────────
# 访问麦肯锡的请求走此代理；飞书 API 直连。留空则不使用代理。
PROXY_URL = os.getenv("PROXY_URL", "")

# ── 麦肯锡中国 ────────────────────────────────────────────
MCKINSEY_BASE = "https://www.mckinsey.com.cn"

# 主题 → 分类列表页路径。站点为 WordPress，无公开搜索 API，
# 按分类列表抓取比关键词搜索更稳。
TOPIC_CATEGORY_PATHS = {
    "ai": "/insights/business-technology/",   # 数字化
    "automotive": "/insights/autos/",         # 汽车
    "design": "/insights/innovation/",        # 创新
}

TOPIC_LABELS = {
    "ai": "AI",
    "automotive": "汽车",
    "design": "创新",
}

# ── 爬取参数 ───────────────────────────────────────────────
DEFAULT_LIMIT_PER_TOPIC = 5

# 两篇文章之间的节拍（秒）。低频运行场景建议 30-90，避免触发限流。
REQUEST_DELAY_SECONDS = 60

# ── 文章详情 / 列表页抓取 ────────────────────────────────
# 拆分连接 / 读取超时：连接快失败，读取给足时间。
# 列表页与正文共用该设置；站点首字节偶有 30s+ 延迟，所以读取超时要给大些。
ARTICLE_FETCH_TIMEOUT_CONNECT = 10
ARTICLE_FETCH_TIMEOUT_READ = 180

# 请求失败后的退避（秒）。总尝试次数 = 1 + len(schedule)。
ARTICLE_FETCH_BACKOFF_SCHEDULE = [5, 10]

# curl_cffi 浏览器指纹池（按尝试轮换）。新指纹被 WAF 收录需要时间，
# 失败时把更新的版本追加到最前面即可。
ARTICLE_FETCH_IMPERSONATE_POOL = ["chrome131", "chrome133", "chrome124"]

# ── Playwright 兜底 ───────────────────────────────────────
# requests 全部失败时启动无头浏览器兜底。
PLAYWRIGHT_FALLBACK_ENABLED = True
PLAYWRIGHT_TIMEOUT_MS = 90_000
# 优先使用系统 Chrome；不可用时回退到自带 Chromium。
PLAYWRIGHT_CHANNEL = "chrome"
PLAYWRIGHT_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
PLAYWRIGHT_STEALTH_JS = (
    "Object.defineProperty(navigator, 'webdriver', { get: () => false });"
)

# ── 全文判定 / 分页 ──────────────────────────────────────
FULLTEXT_MIN_PARAGRAPHS = 6
CONTENT_STABLE_ROUNDS = 2
# 单篇文章多段分页时最多走几页（含首页）。
PAGINATION_MAX_PAGES = 3
# 分类列表翻页上限（WordPress /page/N/）。
CATEGORY_LIST_MAX_PAGES = 3

# ── 运行状态 ──────────────────────────────────────────────
RUN_STATE_PATH = os.path.join(os.path.dirname(__file__), "run_state.json")


def validate_config() -> list[str]:
    """返回缺失的配置项列表"""
    required = {
        "FEISHU_APP_ID": FEISHU_APP_ID,
        "FEISHU_APP_SECRET": FEISHU_APP_SECRET,
        "FEISHU_BITABLE_APP_TOKEN": FEISHU_BITABLE_APP_TOKEN,
        "FEISHU_BITABLE_TABLE_ID": FEISHU_BITABLE_TABLE_ID,
        "FEISHU_FOLDER_TOKEN": FEISHU_FOLDER_TOKEN,
    }
    return [k for k, v in required.items() if not v]
