"""
麦肯锡文章爬虫

抓取链路：
- 列表：麦肯锡搜索 API（普通 requests 即可，JSON 接口无反爬）
- 详情：curl_cffi (Chrome TLS 指纹) → 失败/被登录墙拦截则 Playwright 兜底
- 登录：login_helper.py 一次性保存 storage_state，之后两条路径都自动带上登录 cookies
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_cffi_requests

from config import (
    ARTICLE_FETCH_BACKOFF_SCHEDULE,
    ARTICLE_FETCH_IMPERSONATE,
    ARTICLE_FETCH_TIMEOUT_CONNECT,
    ARTICLE_FETCH_TIMEOUT_READ,
    CONTINUE_READING_MARKERS,
    CONTINUE_READING_SELECTORS,
    LOGIN_WALL_MARKERS,
    MCKINSEY_BASE,
    MCKINSEY_SEARCH_API,
    PLAYWRIGHT_CHANNEL,
    PLAYWRIGHT_FALLBACK_ENABLED,
    PLAYWRIGHT_LAUNCH_ARGS,
    PLAYWRIGHT_MAX_CONTINUE_CLICKS,
    PLAYWRIGHT_STEALTH_JS,
    PLAYWRIGHT_STORAGE_STATE_PATH,
    PLAYWRIGHT_TIMEOUT_MS,
    PROXY_URL,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
)

_PROXY_DICT = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

logger = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

_API_HEADERS = {
    "User-Agent": _BROWSER_HEADERS["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.mckinsey.com",
    "Referer": "https://www.mckinsey.com/",
}

_CONTENT_EXTRA_HEADERS = {
    "Referer": "https://www.mckinsey.com/",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
}


def _emit(tag: str, msg: str) -> None:
    """向 stdout 打印结构化进度行，skill agent 会转发给用户"""
    print(f"[{tag}] {msg}", flush=True)


def _contains_any(html: str, markers: list[str]) -> bool:
    """html 是否包含 markers 中任一子串（大小写不敏感）"""
    if not html:
        return False
    lower = html.lower()
    return any(m.lower() in lower for m in markers)


@dataclass
class Article:
    title: str
    url: str
    summary: str = ""
    date: str = ""
    topic: str = ""
    authors: str = ""
    content_type: str = ""
    content_paragraphs: list[str] = field(default_factory=list)


class _CffiEscalateError(Exception):
    """curl_cffi 响应收到但内容被登录墙/预览墙拦截 —— 直接切 Playwright，不重试。"""


class McKinseyScraper:
    """基于麦肯锡搜索 API + curl_cffi (+ Playwright 兜底) 的爬虫"""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(_BROWSER_HEADERS)
        if _PROXY_DICT:
            self._session.proxies.update(_PROXY_DICT)

        self._cffi_session = None
        self._cffi_warmed_up = False

        self._playwright = None
        self._browser = None
        self._browser_context = None

        # 登录状态（由 login_helper.py 生成）
        self._has_storage_state = os.path.exists(PLAYWRIGHT_STORAGE_STATE_PATH)
        if self._has_storage_state:
            logger.info(f"检测到登录状态文件: {PLAYWRIGHT_STORAGE_STATE_PATH}")

    # ─────────────────────────────────────────────────────
    # 搜索 API
    # ─────────────────────────────────────────────────────

    def search_articles(
        self, keyword: str, limit: int = 10, sort: str = "default"
    ) -> list[Article]:
        articles = []
        seen_urls = set()
        start = 1

        while len(articles) < limit:
            params = {
                "q": keyword,
                "start": start,
                "sort": sort,
                "pageFilter": "all",
            }

            try:
                logger.info(f"搜索 API: keyword={keyword!r}, start={start}")
                resp = self._session.get(
                    MCKINSEY_SEARCH_API,
                    params=params,
                    headers=_API_HEADERS,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"搜索 API 请求失败: {e}")
                break

            if data.get("status") != "OK":
                logger.error(f"搜索 API 返回异常: {data.get('statusMessage')}")
                break

            results = data.get("results", [])
            if not results:
                break

            for item in results:
                if len(articles) >= limit:
                    break

                url = item.get("url") or item.get("metatag.url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                title = item.get("metatag.title", "").strip()
                if not title:
                    continue

                summary = (item.get("description") or "").strip()
                article = Article(
                    title=title,
                    url=url,
                    summary=summary,
                    date=self._parse_api_date(
                        item.get("metatag.itemdate", "")
                    ),
                    authors=item.get("metatag.authors-name", "") or "",
                    content_type=item.get("metatag.contenttype", ""),
                )
                articles.append(article)

            num_found = data.get("numFound", 0)
            start += len(results)
            if start > num_found:
                break

            time.sleep(1)

        logger.info(f"关键词 [{keyword}] 共获取 {len(articles)} 篇文章")
        return articles

    def search_topic(
        self, keywords: list[str], limit: int = 10
    ) -> list[Article]:
        all_articles: dict[str, Article] = {}
        per_keyword_limit = max(limit, 10)

        for kw in keywords:
            results = self.search_articles(
                kw, limit=per_keyword_limit, sort="newest"
            )
            for a in results:
                if a.url not in all_articles:
                    all_articles[a.url] = a

        sorted_articles = sorted(
            all_articles.values(),
            key=lambda a: a.date or "",
            reverse=True,
        )
        return sorted_articles[:limit]

    # ─────────────────────────────────────────────────────
    # 文章详情主入口
    # ─────────────────────────────────────────────────────

    def fetch_article_content(self, article: Article) -> Article:
        """
        抓取文章正文。
        流程：warmup → curl_cffi 重试 → 遇登录墙/预览墙即刻切 Playwright →
        Playwright 加载 storage_state、点击"继续阅读"、滚到底提取全文。
        """
        self._warmup_cffi()

        last_error: Exception | None = None
        cffi_escalate = False  # True 表示响应到了但被拦，跳过重试
        success = False

        # ── 主路径：curl_cffi + 指数退避 ──
        attempts = 1 + len(ARTICLE_FETCH_BACKOFF_SCHEDULE)
        for attempt_idx in range(attempts):
            if attempt_idx > 0:
                backoff = ARTICLE_FETCH_BACKOFF_SCHEDULE[attempt_idx - 1]
                _emit(
                    "PROGRESS",
                    f"    curl_cffi 第 {attempt_idx}/{attempts - 1} 次重试前等待 {backoff}s",
                )
                time.sleep(backoff)

            try:
                logger.info(
                    f"curl_cffi 抓取: {article.url} "
                    f"(attempt {attempt_idx + 1}/{attempts})"
                )
                html = self._fetch_html_via_cffi(article.url)
                self._populate_article_from_html(article, html)
                logger.info(
                    f"  curl_cffi 成功: {len(article.content_paragraphs)} 段"
                )
                success = True
                break
            except _CffiEscalateError as e:
                last_error = e
                cffi_escalate = True
                _emit(
                    "PROGRESS",
                    f"    curl_cffi: {e}，跳过重试直接用 Playwright",
                )
                break
            except Exception as e:
                last_error = e
                short_err = str(e)[:160]
                _emit(
                    "PROGRESS",
                    f"    curl_cffi {attempt_idx + 1}/{attempts} 失败: {short_err}",
                )

                if "403" in short_err or "HTTP Error 403" in short_err:
                    _emit(
                        "PROGRESS",
                        "    检测到 403，跳过剩余 curl_cffi 重试，直接切 Playwright",
                    )
                    break

        # ── 兜底：Playwright ──
        if not success:
            if not PLAYWRIGHT_FALLBACK_ENABLED:
                time.sleep(REQUEST_DELAY_SECONDS)
                raise RuntimeError(f"curl_cffi 全部失败: {last_error}")

            try:
                reason = "被拦截" if cffi_escalate else "重试用尽"
                _emit("PROGRESS", f"    curl_cffi {reason}，启动 Playwright 兜底")
                html = self._fetch_html_via_playwright(article.url)
                self._populate_article_from_html(article, html)
                _emit(
                    "PROGRESS",
                    f"    Playwright 成功: {len(article.content_paragraphs)} 段",
                )
            except Exception as pw_err:
                time.sleep(REQUEST_DELAY_SECONDS)
                hint = ""
                if not self._has_storage_state:
                    hint = "（该文章可能需要登录，请先运行 python3 login_helper.py）"
                raise RuntimeError(
                    f"curl_cffi 失败 [{last_error}]；"
                    f"Playwright 兜底失败 [{pw_err}]{hint}"
                )

        time.sleep(REQUEST_DELAY_SECONDS)
        return article

    # ── curl_cffi 子步骤 ─────────────────────────────────

    def _get_cffi_session(self):
        if self._cffi_session is None:
            self._cffi_session = curl_cffi_requests.Session(
                impersonate=ARTICLE_FETCH_IMPERSONATE,
                proxies=_PROXY_DICT,
            )
            self._cffi_session.headers.update(_CONTENT_EXTRA_HEADERS)
            self._apply_storage_state_cookies()
        return self._cffi_session

    def _apply_storage_state_cookies(self) -> None:
        """把 Playwright 保存的登录 cookies 注入到 curl_cffi session"""
        if not self._has_storage_state:
            return
        try:
            with open(PLAYWRIGHT_STORAGE_STATE_PATH) as f:
                state = json.load(f)
            loaded = 0
            for c in state.get("cookies", []):
                domain = c.get("domain", "")
                if "mckinsey.com" not in domain:
                    continue
                try:
                    self._cffi_session.cookies.set(
                        c["name"],
                        c["value"],
                        domain=domain,
                        path=c.get("path", "/"),
                    )
                    loaded += 1
                except Exception:
                    continue
            logger.info(f"curl_cffi 已加载 {loaded} 个登录 cookies")
        except Exception as e:
            logger.warning(f"curl_cffi 加载登录状态失败: {e}")

    def _warmup_cffi(self) -> None:
        if self._cffi_warmed_up:
            return
        try:
            sess = self._get_cffi_session()
            sess.get(
                MCKINSEY_BASE,
                timeout=(ARTICLE_FETCH_TIMEOUT_CONNECT, 30),
                allow_redirects=True,
            )
            logger.info("curl_cffi 已预热")
        except Exception as e:
            logger.warning(f"curl_cffi 预热失败（继续尝试抓取）: {e}")
        finally:
            self._cffi_warmed_up = True

    def _fetch_html_via_cffi(self, url: str) -> str:
        """成功返回 HTML；检出登录墙/预览墙抛 _CffiEscalateError；其他错误原样抛。"""
        sess = self._get_cffi_session()
        resp = sess.get(
            url,
            timeout=(ARTICLE_FETCH_TIMEOUT_CONNECT, ARTICLE_FETCH_TIMEOUT_READ),
            allow_redirects=True,
        )
        resp.raise_for_status()
        html = resp.text

        if _contains_any(html, LOGIN_WALL_MARKERS):
            raise _CffiEscalateError("响应疑似登录墙")
        if _contains_any(html, CONTINUE_READING_MARKERS):
            raise _CffiEscalateError("响应包含'继续阅读'按钮（curl_cffi 无法点击）")

        return html

    # ── Playwright 子步骤 ────────────────────────────────

    def _ensure_playwright(self) -> None:
        if self._browser_context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "Playwright 未安装。请执行:\n"
                "  pip3 install playwright\n"
                "  python3 -m playwright install chromium"
            ) from e

        self._playwright = sync_playwright().start()

        # 优先使用系统 Chrome（TLS 指纹真实，不被 Akamai 拦截）
        launch_kwargs = {"headless": True, "args": PLAYWRIGHT_LAUNCH_ARGS}
        if PROXY_URL:
            launch_kwargs["proxy"] = {"server": PROXY_URL}
        if PLAYWRIGHT_CHANNEL:
            try:
                launch_kwargs["channel"] = PLAYWRIGHT_CHANNEL
                self._browser = self._playwright.chromium.launch(**launch_kwargs)
            except Exception:
                _emit("PROGRESS", "    系统 Chrome 不可用，回退到自带 Chromium")
                launch_kwargs.pop("channel", None)
                self._browser = self._playwright.chromium.launch(**launch_kwargs)
        else:
            self._browser = self._playwright.chromium.launch(**launch_kwargs)

        ctx_kwargs = {
            "viewport": {"width": 1440, "height": 900},
            "user_agent": _BROWSER_HEADERS["User-Agent"],
            "locale": "en-US",
        }
        if self._has_storage_state:
            ctx_kwargs["storage_state"] = PLAYWRIGHT_STORAGE_STATE_PATH
            _emit(
                "PROGRESS",
                f"    Playwright 已加载登录状态 ({os.path.basename(PLAYWRIGHT_STORAGE_STATE_PATH)})",
            )
        self._browser_context = self._browser.new_context(**ctx_kwargs)
        self._browser_context.add_init_script(PLAYWRIGHT_STEALTH_JS)

    def _fetch_html_via_playwright(self, url: str) -> str:
        self._ensure_playwright()
        page = self._browser_context.new_page()
        try:
            page.goto(
                url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT_MS
            )
            self._scroll_to_bottom(page)
            self._click_continue_buttons(page)
            self._scroll_to_bottom(page)
            return page.content()
        finally:
            page.close()

    def _scroll_to_bottom(self, page) -> None:
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1200)
        except Exception:
            pass

    def _click_continue_buttons(self, page) -> None:
        """循环点击已知的'继续阅读'按钮，直到没有可见的为止（最多 N 次）"""
        for _ in range(PLAYWRIGHT_MAX_CONTINUE_CLICKS):
            clicked = False
            for selector in CONTINUE_READING_SELECTORS:
                try:
                    locator = page.locator(selector).first
                    locator.wait_for(state="visible", timeout=1500)
                    locator.scroll_into_view_if_needed(timeout=1500)
                    locator.click(timeout=3000)
                    clicked = True
                    _emit(
                        "PROGRESS",
                        f"    Playwright 点击继续阅读按钮 [{selector}]",
                    )
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        page.wait_for_timeout(2000)
                    break
                except Exception:
                    continue
            if not clicked:
                break

    # ── HTML → Article 字段 ──────────────────────────────

    def _populate_article_from_html(
        self, article: Article, html: str
    ) -> None:
        soup = BeautifulSoup(html, "html.parser")

        if not article.title:
            h1 = soup.find("h1")
            if h1:
                article.title = h1.get_text(strip=True)

        paragraphs: list[str] = []
        content_selectors = [
            {"class_": lambda c: c and "article-body" in " ".join(c)},
            {"class_": lambda c: c and "ArticleBody" in " ".join(c)},
            {"class_": "body-content"},
            "article",
            "main",
        ]

        for sel in content_selectors:
            if isinstance(sel, dict):
                container = soup.find("div", **sel)
            else:
                container = soup.find(sel)

            if container:
                for p in container.find_all("p"):
                    text = p.get_text(strip=True)
                    if text and len(text) > 20:
                        paragraphs.append(text)
                if len(paragraphs) >= 3:
                    break

        if len(paragraphs) < 3:
            for p in soup.find_all("p"):
                text = p.get_text(strip=True)
                if text and len(text) > 50:
                    paragraphs.append(text)

        article.content_paragraphs = paragraphs

    # ── 清理 ─────────────────────────────────────────────

    def close(self) -> None:
        if self._browser_context is not None:
            try:
                self._browser_context.close()
            except Exception as e:
                logger.warning(f"关闭 browser_context 失败: {e}")
            self._browser_context = None

        if self._browser is not None:
            try:
                self._browser.close()
            except Exception as e:
                logger.warning(f"关闭 browser 失败: {e}")
            self._browser = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception as e:
                logger.warning(f"停止 playwright 失败: {e}")
            self._playwright = None

        if self._cffi_session is not None:
            try:
                self._cffi_session.close()
            except Exception:
                pass
            self._cffi_session = None

    # ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_api_date(iso_str: str) -> str:
        if not iso_str:
            return ""
        try:
            return iso_str[:10]
        except Exception:
            return iso_str
