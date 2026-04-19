"""飞书 API 和麦肯锡爬取配置"""

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
# 所有访问麦肯锡的请求（搜索 API / curl_cffi / Playwright）都走此代理。
# 飞书 API 不走代理。留空则直连。
# 示例:
#   socks5://127.0.0.1:1080
#   http://user:pass@proxy.example.com:8080
#   socks5://user:pass@127.0.0.1:7890
PROXY_URL = os.getenv("PROXY_URL", "")

# ── 麦肯锡搜索 API ────────────────────────────────────────
MCKINSEY_SEARCH_API = (
    "https://gateway.mckinsey.com/apigw-x0cceuow60/v1/api/pages"
)
MCKINSEY_BASE = "https://www.mckinsey.com"

# 每个主题对应的搜索关键词（可配置多个，取并集去重）
TOPIC_KEYWORDS = {
    "ai": ["artificial intelligence", "generative AI", "AI transformation"],
    "automotive": ["automotive", "electric vehicles", "mobility"],
    "design": ["industrial design", "product design", "advanced manufacturing"],
}

TOPIC_LABELS = {
    "ai": "AI",
    "automotive": "汽车",
    "design": "工业设计",
}

# ── 爬取参数 ───────────────────────────────────────────────
DEFAULT_LIMIT_PER_TOPIC = 5

# 搜索 API 超时（JSON 接口，通常很快）
REQUEST_TIMEOUT_SECONDS = 30

# 两篇文章之间的节拍（秒）。低频运行场景下建议 30-90，避免触发速率限制。
REQUEST_DELAY_SECONDS = 60

# ── 文章详情抓取（curl_cffi 主路径） ───────────────────────
# 拆分连接 / 读取超时：连接快失败（网络不通立刻知道），读取给足时间
ARTICLE_FETCH_TIMEOUT_CONNECT = 10
ARTICLE_FETCH_TIMEOUT_READ = 180

# 403/抓取失败后的退避序列（秒）。列表长度 = 重试次数。
# 这里故意保持很短，失败后尽快切到 Playwright，避免单篇文章卡太久。
ARTICLE_FETCH_BACKOFF_SCHEDULE = [5]

# curl_cffi 浏览器指纹池（按尝试轮换）。
# 常用: chrome120 / chrome124 / chrome131
ARTICLE_FETCH_IMPERSONATE_POOL = ["chrome124", "chrome131"]

# ── Playwright 兜底 ───────────────────────────────────────
# 当 curl_cffi 全部重试失败、或 curl_cffi 响应疑似被登录墙/付费墙拦截时，
# 启动无头 Chromium 兜底。
PLAYWRIGHT_FALLBACK_ENABLED = True
# 单篇 Playwright 页面加载超时（毫秒）
PLAYWRIGHT_TIMEOUT_MS = 90_000

# ── Playwright 反检测 ─────────────────────────────────────
# Playwright 自带 Chromium 的 TLS 指纹（JA3/JA4）和真正 Chrome 不一样，
# Akamai Bot Manager 在 HTTP 层就能把它拦住。
# 设为 "chrome" 让 Playwright 调用系统安装的 Google Chrome 而不是自带 Chromium，
# TLS 握手与真实浏览器一致。如果目标机器无系统 Chrome，设为 "" 回退到 Chromium。
PLAYWRIGHT_CHANNEL = "chrome"
# 关闭 Blink 的 AutomationControlled 特性标记
PLAYWRIGHT_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
# 页面 JS 之前注入：隐藏 navigator.webdriver 标记
PLAYWRIGHT_STEALTH_JS = (
    "Object.defineProperty(navigator, 'webdriver', { get: () => false });"
)

# ── 登录状态持久化 ────────────────────────────────────────
# 一次性运行 `python3 login_helper.py` 手动登录后，状态会保存到这个文件。
# 之后 Playwright 和 curl_cffi 都会自动加载，避免每次都遇登录墙。
# 注意：此文件包含会话 cookies，请勿提交到 git 或分享他人。
PLAYWRIGHT_STORAGE_STATE_PATH = os.path.join(
    os.path.dirname(__file__), "playwright_state.json"
)

# curl_cffi 响应中出现以下任一子串时，视为被登录墙拦截，
# 立即跳过剩余重试切到 Playwright。
# 实际遇到假阳/假阴时，调整这里即可。
LOGIN_WALL_MARKERS = [
    "Sign in to read",
    "Sign in to continue",
    "Register to continue",
    "Please sign in or register",
    "Create a free account",
    "create your free account",
    "Unlock this",
    "subscribers only",
]

# curl_cffi 响应中出现以下任一子串，说明是"预览 + 继续阅读"模式，
# curl_cffi 无法点击，直接切到 Playwright。
CONTINUE_READING_MARKERS = [
    ">Continue reading<",
    ">Read more<",
    ">Show full article<",
    'aria-label="Continue reading"',
    'data-component="ReadMore',
]

# Playwright 用来定位"继续阅读"按钮的选择器，按顺序尝试。
# 第一个能找到的可见元素会被点击，然后等 networkidle / 滚到底。
CONTINUE_READING_SELECTORS = [
    'button:has-text("Continue reading")',
    'button:has-text("Read more")',
    'button:has-text("Show full article")',
    'a:has-text("Continue reading")',
    'a:has-text("Read the full article")',
    '[aria-label*="Continue reading"]',
    '[data-component*="ReadMore"]',
]

# 每个 Playwright 页面最多连续点 N 次（防多段分页）
PLAYWRIGHT_MAX_CONTINUE_CLICKS = 3

# ── 全文判定 / 分页 ──────────────────────────────────────
# 抽取段落少于该值时，视为正文不完整。
FULLTEXT_MIN_PARAGRAPHS = 6
# 连续 N 轮内容无增长，判定页面已经稳定。
CONTENT_STABLE_ROUNDS = 2
# 分页抓取的最大页数（含首页）。
PAGINATION_MAX_PAGES = 3

# ── 认证保活 ──────────────────────────────────────────────
# 登录态剩余有效期小于该阈值（小时）时自动触发刷新。
AUTH_REFRESH_THRESHOLD_HOURS = 12

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
