"""
麦肯锡文章爬虫

抓取链路：
- 列表：麦肯锡搜索 API（requests）
- 详情：curl_cffi（TLS 指纹轮换）→ Playwright 持久上下文兜底
- 登录：storage_state + 自动刷新（由 main/auth_manager 协同触发）
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_cffi_requests

from config import (
    ARTICLE_FETCH_BACKOFF_SCHEDULE,
    ARTICLE_FETCH_IMPERSONATE_POOL,
    ARTICLE_FETCH_TIMEOUT_CONNECT,
    ARTICLE_FETCH_TIMEOUT_READ,
    CONTENT_STABLE_ROUNDS,
    CONTINUE_READING_MARKERS,
    CONTINUE_READING_SELECTORS,
    FULLTEXT_MIN_PARAGRAPHS,
    LOGIN_WALL_MARKERS,
    MCKINSEY_BASE,
    MCKINSEY_SEARCH_API,
    PAGINATION_MAX_PAGES,
    PLAYWRIGHT_CHANNEL,
    PLAYWRIGHT_FALLBACK_ENABLED,
    PLAYWRIGHT_LAUNCH_ARGS,
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

_TRUNCATION_TAIL_MARKERS = [
    "continue reading",
    "read more",
    "show full article",
    "sign in to read",
    "register to continue",
]


def _emit(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def _contains_any(html: str, markers: list[str]) -> bool:
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
    pass


class _IncompleteContentError(Exception):
    pass


class McKinseyScraper:
    """基于搜索 API + cffi + Playwright 持久上下文的爬虫。"""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(_BROWSER_HEADERS)
        if _PROXY_DICT:
            self._session.proxies.update(_PROXY_DICT)

        self._cffi_sessions: dict[str, object] = {}
        self._cffi_warmed_up = False

        self._playwright = None
        self._browser_context = None

        self._has_storage_state = os.path.exists(PLAYWRIGHT_STORAGE_STATE_PATH)
        self._playwright_user_data_dir = os.path.join(
            os.path.dirname(PLAYWRIGHT_STORAGE_STATE_PATH),
            "playwright-user-data",
        )
        os.makedirs(self._playwright_user_data_dir, exist_ok=True)

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
                    date=self._parse_api_date(item.get("metatag.itemdate", "")),
                    authors=item.get("metatag.authors-name", "") or "",
                    content_type=item.get("metatag.contenttype", ""),
                )
                articles.append(article)

            num_found = data.get("numFound", 0)
            start += len(results)
            if start > num_found:
                break

            time.sleep(1)

        return articles

    def search_topic(
        self, keywords: list[str], limit: int = 10
    ) -> list[Article]:
        all_articles: dict[str, Article] = {}
        per_keyword_limit = max(limit, 10)

        for kw in keywords:
            results = self.search_articles(kw, limit=per_keyword_limit, sort="newest")
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

    def fetch_article_content(
        self,
        article: Article,
        *,
        require_full_content: bool = True,
        auth_refresh_handler: Optional[Callable[[], bool]] = None,
    ) -> Article:
        self._warmup_cffi()

        retried_after_auth_refresh = False
        last_error: Optional[Exception] = None

        while True:
            try:
                self._fetch_article_content_once(
                    article,
                    require_full_content=require_full_content,
                )
                time.sleep(REQUEST_DELAY_SECONDS)
                return article
            except Exception as e:
                last_error = e
                if retried_after_auth_refresh or auth_refresh_handler is None:
                    break

                _emit("PROGRESS", "    启动登录态刷新后重试该文章（仅一次）")
                refreshed = auth_refresh_handler()
                if not refreshed:
                    break

                self._reset_runtime_clients()
                retried_after_auth_refresh = True

        time.sleep(REQUEST_DELAY_SECONDS)
        raise RuntimeError(str(last_error))

    def _fetch_article_content_once(
        self,
        article: Article,
        *,
        require_full_content: bool,
    ) -> None:
        last_error: Optional[Exception] = None
        cffi_escalate = False
        success = False

        fingerprint_pool = ARTICLE_FETCH_IMPERSONATE_POOL or ["chrome124"]
        attempts = max(1, 1 + len(ARTICLE_FETCH_BACKOFF_SCHEDULE))

        for attempt_idx in range(attempts):
            if attempt_idx > 0:
                backoff = ARTICLE_FETCH_BACKOFF_SCHEDULE[attempt_idx - 1]
                _emit("PROGRESS", f"    curl_cffi 重试前等待 {backoff}s")
                time.sleep(backoff)

            impersonate = fingerprint_pool[attempt_idx % len(fingerprint_pool)]

            try:
                html = self._fetch_html_via_cffi(article.url, impersonate=impersonate)
                self._populate_article_from_html(article, html)
                if require_full_content:
                    ok, reason = self._is_full_text_complete(
                        html,
                        article.content_paragraphs,
                    )
                    if not ok:
                        raise _IncompleteContentError(reason)
                success = True
                _emit(
                    "PROGRESS",
                    f"    curl_cffi 成功（指纹 {impersonate}，{len(article.content_paragraphs)} 段）",
                )
                break
            except _CffiEscalateError as e:
                last_error = e
                cffi_escalate = True
                _emit("PROGRESS", f"    curl_cffi: {e}，直接切 Playwright")
                break
            except Exception as e:
                last_error = e
                short_err = str(e)[:180]
                _emit("PROGRESS", f"    curl_cffi 失败（{impersonate}）: {short_err}")
                if "403" in short_err or "HTTP Error 403" in short_err:
                    _emit("PROGRESS", "    检测到 403，直接切 Playwright")
                    break

        if success:
            return

        if not PLAYWRIGHT_FALLBACK_ENABLED:
            raise RuntimeError(f"curl_cffi 全部失败: {last_error}")

        reason = "被拦截" if cffi_escalate else "重试用尽"
        _emit("PROGRESS", f"    curl_cffi {reason}，启动 Playwright 持久上下文兜底")
        html = self._fetch_html_via_playwright(article.url)
        self._populate_article_from_html(article, html)

        if require_full_content:
            ok, complete_reason = self._is_full_text_complete(
                html,
                article.content_paragraphs,
            )
            if not ok:
                raise _IncompleteContentError(complete_reason)

        _emit("PROGRESS", f"    Playwright 成功: {len(article.content_paragraphs)} 段")

    # ── curl_cffi 子步骤 ─────────────────────────────────

    def _get_cffi_session(self, impersonate: str):
        if impersonate not in self._cffi_sessions:
            sess = curl_cffi_requests.Session(
                impersonate=impersonate,
                proxies=_PROXY_DICT,
            )
            sess.headers.update(_CONTENT_EXTRA_HEADERS)
            self._apply_storage_state_cookies(sess)
            self._cffi_sessions[impersonate] = sess
        return self._cffi_sessions[impersonate]

    def _apply_storage_state_cookies(self, session) -> None:
        if not os.path.exists(PLAYWRIGHT_STORAGE_STATE_PATH):
            return
        try:
            with open(PLAYWRIGHT_STORAGE_STATE_PATH, encoding="utf-8") as f:
                state = json.load(f)
            for c in state.get("cookies", []):
                domain = c.get("domain", "")
                if "mckinsey.com" not in domain:
                    continue
                try:
                    session.cookies.set(
                        c["name"],
                        c["value"],
                        domain=domain,
                        path=c.get("path", "/"),
                    )
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"curl_cffi 加载登录状态失败: {e}")

    def _warmup_cffi(self) -> None:
        if self._cffi_warmed_up:
            return
        try:
            first_imp = (ARTICLE_FETCH_IMPERSONATE_POOL or ["chrome124"])[0]
            sess = self._get_cffi_session(first_imp)
            sess.get(
                MCKINSEY_BASE,
                timeout=(ARTICLE_FETCH_TIMEOUT_CONNECT, 30),
                allow_redirects=True,
            )
        except Exception as e:
            logger.warning(f"curl_cffi 预热失败（继续抓取）: {e}")
        finally:
            self._cffi_warmed_up = True

    def _fetch_html_via_cffi(self, url: str, *, impersonate: str) -> str:
        sess = self._get_cffi_session(impersonate)
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
            raise _CffiEscalateError("响应包含继续阅读按钮")

        return html

    # ── Playwright 子步骤 ────────────────────────────────

    def _ensure_playwright_context(self) -> None:
        if self._browser_context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "Playwright 未安装。请执行: pip3 install playwright && python3 -m playwright install chromium"
            ) from e

        self._playwright = sync_playwright().start()

        launch_kwargs = {
            "headless": True,
            "args": PLAYWRIGHT_LAUNCH_ARGS,
            "viewport": {"width": 1440, "height": 900},
            "user_agent": _BROWSER_HEADERS["User-Agent"],
            "locale": "en-US",
        }
        if PROXY_URL:
            launch_kwargs["proxy"] = {"server": PROXY_URL}

        chromium = self._playwright.chromium
        if PLAYWRIGHT_CHANNEL:
            try:
                launch_kwargs["channel"] = PLAYWRIGHT_CHANNEL
                self._browser_context = chromium.launch_persistent_context(
                    self._playwright_user_data_dir,
                    **launch_kwargs,
                )
            except Exception:
                _emit("PROGRESS", "    系统 Chrome 不可用，回退到 Chromium")
                launch_kwargs.pop("channel", None)

        if self._browser_context is None:
            self._browser_context = chromium.launch_persistent_context(
                self._playwright_user_data_dir,
                **launch_kwargs,
            )

        self._browser_context.add_init_script(PLAYWRIGHT_STEALTH_JS)
        self._apply_storage_state_to_playwright_context()

    def _apply_storage_state_to_playwright_context(self) -> None:
        if self._browser_context is None:
            return
        if not os.path.exists(PLAYWRIGHT_STORAGE_STATE_PATH):
            return
        try:
            with open(PLAYWRIGHT_STORAGE_STATE_PATH, encoding="utf-8") as f:
                state = json.load(f)
            cookies = []
            for cookie in state.get("cookies", []):
                domain = (cookie.get("domain") or "").lower()
                if "mckinsey.com" not in domain:
                    continue
                expires = cookie.get("expires", -1)
                c = {
                    "name": cookie.get("name", ""),
                    "value": cookie.get("value", ""),
                    "path": cookie.get("path", "/"),
                    "httpOnly": bool(cookie.get("httpOnly", False)),
                    "secure": bool(cookie.get("secure", True)),
                    "sameSite": cookie.get("sameSite", "Lax"),
                }
                if domain.startswith("."):
                    c["domain"] = domain
                else:
                    c["domain"] = domain
                if isinstance(expires, (int, float)) and expires > 0:
                    c["expires"] = expires
                cookies.append(c)

            if cookies:
                self._browser_context.add_cookies(cookies)
                _emit("PROGRESS", "    Playwright 已注入登录 cookies")
                self._has_storage_state = True
        except Exception as e:
            logger.warning(f"Playwright 注入登录 cookies 失败: {e}")

    def _fetch_html_via_playwright(self, url: str) -> str:
        self._ensure_playwright_context()

        html_parts: list[str] = []
        visited: set[str] = set()
        current_url = url

        for page_index in range(PAGINATION_MAX_PAGES):
            if not current_url or current_url in visited:
                break
            visited.add(current_url)

            page = self._browser_context.new_page()
            try:
                page.goto(current_url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT_MS)
                self._expand_content_until_stable(page)
                html = page.content()
                html_parts.append(html)

                next_url = self.discover_next_page_from_html(
                    html,
                    current_url,
                    visited,
                )
                if next_url and page_index + 1 < PAGINATION_MAX_PAGES:
                    _emit("PROGRESS", f"    发现下一页，继续抓取: {next_url}")
                current_url = next_url
            finally:
                page.close()

        if not html_parts:
            raise RuntimeError("Playwright 未抓取到页面内容")

        return "\n<!-- SC_PAGE_BREAK -->\n".join(html_parts)

    def _expand_content_until_stable(self, page) -> None:
        stable_rounds = 0
        prev_signal = (-1, -1)

        while stable_rounds < CONTENT_STABLE_ROUNDS:
            self._scroll_to_bottom(page)
            clicked = self._click_continue_button_once(page)
            self._scroll_to_bottom(page)

            current_signal = self._content_signal(page)
            if current_signal == prev_signal:
                stable_rounds += 1
            else:
                stable_rounds = 0
            prev_signal = current_signal

            if clicked:
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    page.wait_for_timeout(1800)
            else:
                page.wait_for_timeout(1200)

    def _scroll_to_bottom(self, page) -> None:
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
        except Exception:
            return

    def _click_continue_button_once(self, page) -> bool:
        for selector in CONTINUE_READING_SELECTORS:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=1200)
                locator.scroll_into_view_if_needed(timeout=1000)
                locator.click(timeout=2500)
                _emit("PROGRESS", f"    Playwright 点击继续阅读 [{selector}]")
                return True
            except Exception:
                continue
        return False

    def _content_signal(self, page) -> tuple[int, int]:
        try:
            signal = page.evaluate(
                """
                () => {
                  const container = document.querySelector('article, main, div.article-body, div[class*=ArticleBody], div.body-content') || document.body;
                  const paragraphs = container.querySelectorAll('p');
                  const paragraphCount = paragraphs.length;
                  const textLen = (container.innerText || '').trim().length;
                  return { paragraphCount, textLen };
                }
                """
            )
            return int(signal.get("paragraphCount", 0)), int(signal.get("textLen", 0))
        except Exception:
            return (0, 0)

    @staticmethod
    def discover_next_page_from_html(
        html: str,
        current_url: str,
        visited: set[str],
    ) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        candidates: list[str] = []

        for node in soup.select("link[rel='next'][href], a[rel='next'][href]"):
            href = node.get("href")
            if href:
                candidates.append(href)

        for a in soup.find_all("a", href=True):
            text = " ".join(a.get_text(" ", strip=True).lower().split())
            href = a.get("href")
            if not href:
                continue
            if "page=" in href or text in {"next", "next page"} or " next" in text:
                candidates.append(href)

        current_host = urlparse(current_url).netloc
        for href in candidates:
            absolute = urljoin(current_url, href)
            parsed = urlparse(absolute)
            if not parsed.scheme.startswith("http"):
                continue
            if parsed.netloc != current_host:
                continue
            if absolute in visited:
                continue
            if absolute.rstrip("/") == current_url.rstrip("/"):
                continue
            return absolute

        return None

    # ── HTML → Article 字段 ──────────────────────────────

    def _populate_article_from_html(self, article: Article, html: str) -> None:
        soup = BeautifulSoup(html, "html.parser")

        if not article.title:
            h1 = soup.find("h1")
            if h1:
                article.title = h1.get_text(strip=True)

        paragraphs: list[str] = []
        seen: set[str] = set()

        selectors = [
            "div.article-body p",
            "div[class*='ArticleBody'] p",
            "div.body-content p",
            "article p",
            "main p",
        ]

        for sel in selectors:
            for p in soup.select(sel):
                text = p.get_text(" ", strip=True)
                text = " ".join(text.split())
                if len(text) < 20:
                    continue
                if text in seen:
                    continue
                seen.add(text)
                paragraphs.append(text)

            if len(paragraphs) >= FULLTEXT_MIN_PARAGRAPHS:
                break

        if len(paragraphs) < FULLTEXT_MIN_PARAGRAPHS:
            for p in soup.find_all("p"):
                text = p.get_text(" ", strip=True)
                text = " ".join(text.split())
                if len(text) < 50:
                    continue
                if text in seen:
                    continue
                seen.add(text)
                paragraphs.append(text)

        article.content_paragraphs = paragraphs

    def _is_full_text_complete(
        self,
        html: str,
        paragraphs: list[str],
    ) -> tuple[bool, str]:
        if _contains_any(html, LOGIN_WALL_MARKERS):
            return False, "内容疑似登录墙"
        if _contains_any(html, CONTINUE_READING_MARKERS):
            return False, "内容仍含继续阅读按钮"

        if len(paragraphs) < FULLTEXT_MIN_PARAGRAPHS:
            return False, f"正文段落不足（{len(paragraphs)} < {FULLTEXT_MIN_PARAGRAPHS}）"

        total_chars = sum(len(p) for p in paragraphs)
        if total_chars < FULLTEXT_MIN_PARAGRAPHS * 80:
            return False, "正文总长度不足，疑似仅摘要"

        tail = paragraphs[-1].lower() if paragraphs else ""
        if any(marker in tail for marker in _TRUNCATION_TAIL_MARKERS):
            return False, "末段疑似截断"

        return True, "ok"

    # ── 清理 ─────────────────────────────────────────────

    def _reset_runtime_clients(self) -> None:
        for sess in self._cffi_sessions.values():
            try:
                sess.close()
            except Exception:
                pass
        self._cffi_sessions = {}
        self._cffi_warmed_up = False

        if self._browser_context is not None:
            try:
                self._browser_context.close()
            except Exception:
                pass
            self._browser_context = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def close(self) -> None:
        self._reset_runtime_clients()

    @staticmethod
    def _parse_api_date(iso_str: str) -> str:
        if not iso_str:
            return ""
        try:
            return iso_str[:10]
        except Exception:
            return iso_str
