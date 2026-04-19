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

FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"

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

# requests 失败后的退避（秒）。总尝试次数 = 1 + len(schedule)。
ARTICLE_FETCH_BACKOFF_SCHEDULE = [5, 10]

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
