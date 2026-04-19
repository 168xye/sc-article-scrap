"""
麦肯锡中国（mckinsey.com.cn）文章爬虫

抓取链路：
- 列表：WordPress 分类页（requests + BeautifulSoup）
- 详情：requests 主路径，Playwright 兜底
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import (
    ARTICLE_FETCH_BACKOFF_SCHEDULE,
    ARTICLE_FETCH_TIMEOUT_CONNECT,
    ARTICLE_FETCH_TIMEOUT_READ,
    CATEGORY_LIST_MAX_PAGES,
    CONTENT_STABLE_ROUNDS,
    FULLTEXT_MIN_PARAGRAPHS,
    MCKINSEY_BASE,
    PAGINATION_MAX_PAGES,
    PLAYWRIGHT_CHANNEL,
    PLAYWRIGHT_FALLBACK_ENABLED,
    PLAYWRIGHT_LAUNCH_ARGS,
    PLAYWRIGHT_STEALTH_JS,
    PLAYWRIGHT_TIMEOUT_MS,
    PROXY_URL,
    REQUEST_DELAY_SECONDS,
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
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

_DATE_RE = re.compile(r"(20\d{2})[/.\-](\d{1,2})[/.\-](\d{1,2})")

# 列表页中这些路径前缀不视为文章详情。
_NON_ARTICLE_PATH_PREFIXES = (
    "/insights/",
    "/category/",
    "/tag/",
    "/page/",
    "/author/",
    "/contact",
    "/about",
    "/careers",
    "/search",
    "/wp-",
    "/feed",
)


def _emit(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


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


class _IncompleteContentError(Exception):
    pass


class McKinseyScraper:
    """mckinsey.com.cn 爬虫：分类页列表 + 文章正文。"""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update(_BROWSER_HEADERS)
        if _PROXY_DICT:
            self._session.proxies.update(_PROXY_DICT)

        self._playwright = None
        self._browser_context = None
        self._playwright_user_data_dir = os.path.join(
            os.path.dirname(__file__),
            "playwright-user-data",
        )
        os.makedirs(self._playwright_user_data_dir, exist_ok=True)

    # ─────────────────────────────────────────────────────
    # 列表：分类页
    # ─────────────────────────────────────────────────────

    def search_category(self, category_path: str, limit: int = 10) -> list[Article]:
        """按分类路径翻页抓取文章列表。"""
        if not category_path:
            return []

        base = MCKINSEY_BASE.rstrip("/")
        clean_path = "/" + category_path.strip("/") + "/"
        articles: dict[str, Article] = {}

        for page_idx in range(CATEGORY_LIST_MAX_PAGES):
            if len(articles) >= limit:
                break

            if page_idx == 0:
                url = base + clean_path
            else:
                url = base + clean_path + f"page/{page_idx + 1}/"

            html = self._fetch_listing_html(url)
            if html is None:
                break

            found = self._parse_category_page(html, base_url=url)
            new_this_page = 0
            for a in found:
                if a.url not in articles:
                    articles[a.url] = a
                    new_this_page += 1

            if new_this_page == 0:
                break

            time.sleep(1)

        sorted_articles = sorted(
            articles.values(),
            key=lambda a: a.date or "",
            reverse=True,
        )
        return sorted_articles[:limit]

    def search_topic(self, category_path: str, limit: int = 10) -> list[Article]:
        """保留旧名，签名语义变更为「分类路径」。"""
        return self.search_category(category_path, limit=limit)

    def _fetch_listing_html(self, url: str) -> Optional[str]:
        """抓分类列表页：连接快失败、读取给足时间，失败时按退避重试。"""
        attempts = max(1, 1 + len(ARTICLE_FETCH_BACKOFF_SCHEDULE))
        last_error: Optional[Exception] = None

        for attempt_idx in range(attempts):
            if attempt_idx > 0:
                backoff = ARTICLE_FETCH_BACKOFF_SCHEDULE[attempt_idx - 1]
                _emit("PROGRESS", f"    列表页重试前等待 {backoff}s")
                time.sleep(backoff)
            try:
                logger.info(f"分类列表: {url}")
                resp = self._session.get(
                    url,
                    timeout=(
                        ARTICLE_FETCH_TIMEOUT_CONNECT,
                        ARTICLE_FETCH_TIMEOUT_READ,
                    ),
                    allow_redirects=True,
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.text
            except Exception as e:
                last_error = e
                logger.warning(f"分类列表请求失败 ({url}): {e}")
                _emit("PROGRESS", f"    列表页请求失败: {str(e)[:180]}")

        _emit("FAIL", f"列表页重试用尽 ({url}): {last_error}")
        return None

    def _parse_category_page(self, html: str, *, base_url: str) -> list[Article]:
        soup = BeautifulSoup(html, "html.parser")
        base_host = urlparse(base_url).netloc

        # 先找常见 WordPress 文章卡片容器
        card_selectors = (
            "article.post",
            "article[class*='post-']",
            ".post",
            ".entry",
            ".hentry",
            ".post-item",
            ".insight-item",
            ".insights-card",
            ".card",
        )
        seen_nodes: set[int] = set()
        cards = []
        for sel in card_selectors:
            for node in soup.select(sel):
                if id(node) in seen_nodes:
                    continue
                seen_nodes.add(id(node))
                cards.append(node)

        articles: list[Article] = []
        seen_urls: set[str] = set()

        for card in cards:
            article = self._build_article_from_card(card, base_url, base_host)
            if article and article.url not in seen_urls:
                seen_urls.add(article.url)
                articles.append(article)

        if articles:
            return articles

        # 回退：直接扫所有锚点，靠 URL 形态判文章页
        for a_tag in soup.find_all("a", href=True):
            absolute = self._normalize_url(urljoin(base_url, a_tag["href"]))
            if not absolute or not self._is_article_url(absolute, base_host):
                continue
            if absolute in seen_urls:
                continue

            title = a_tag.get_text(" ", strip=True) or (a_tag.get("title") or "").strip()
            if len(title) < 5:
                continue

            date = ""
            ctx = a_tag
            for _ in range(4):
                ctx = ctx.parent if ctx else None
                if not ctx:
                    break
                date = _extract_date(ctx.get_text(" ", strip=True))
                if date:
                    break

            seen_urls.add(absolute)
            articles.append(Article(title=title, url=absolute, date=date))

        return articles

    def _build_article_from_card(
        self,
        card,
        base_url: str,
        base_host: str,
    ) -> Optional[Article]:
        a_tag = None
        for candidate in card.find_all("a", href=True):
            absolute = self._normalize_url(urljoin(base_url, candidate["href"]))
            if absolute and self._is_article_url(absolute, base_host):
                a_tag = candidate
                break
        if a_tag is None:
            return None

        url = self._normalize_url(urljoin(base_url, a_tag["href"]))
        if not url:
            return None

        title = ""
        for sel in ("h1", "h2", "h3", "h4", ".post-title", ".entry-title"):
            h = card.select_one(sel)
            if h:
                title = h.get_text(" ", strip=True)
                if title:
                    break
        if not title:
            title = a_tag.get_text(" ", strip=True) or (a_tag.get("title") or "").strip()
        if not title:
            return None

        date = _extract_date(card.get_text(" ", strip=True))
        return Article(title=title, url=url, date=date)

    @staticmethod
    def _is_article_url(url: str, host: str) -> bool:
        parsed = urlparse(url)
        if parsed.netloc != host:
            return False
        path = parsed.path or ""
        if path in ("", "/"):
            return False
        for prefix in _NON_ARTICLE_PATH_PREFIXES:
            if path.startswith(prefix):
                return False
        return True

    @staticmethod
    def _normalize_url(url: str) -> str:
        """去掉 query/fragment 并保证结尾斜杠，便于稳定去重。"""
        if not url:
            return ""
        parsed = urlparse(url)
        if not parsed.scheme.startswith("http"):
            return ""
        path = parsed.path or "/"
        if not path.endswith("/"):
            path += "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}"

    # ─────────────────────────────────────────────────────
    # 文章详情主入口
    # ─────────────────────────────────────────────────────

    def fetch_article_content(
        self,
        article: Article,
        *,
        require_full_content: bool = True,
    ) -> Article:
        last_error: Optional[Exception] = None
        success = False
        attempts = max(1, 1 + len(ARTICLE_FETCH_BACKOFF_SCHEDULE))

        for attempt_idx in range(attempts):
            if attempt_idx > 0:
                backoff = ARTICLE_FETCH_BACKOFF_SCHEDULE[attempt_idx - 1]
                _emit("PROGRESS", f"    requests 重试前等待 {backoff}s")
                time.sleep(backoff)

            try:
                html = self._fetch_html_via_requests(article.url)
                self._populate_article_from_html(article, html)
                if require_full_content:
                    ok, reason = self._is_full_text_complete(
                        html, article.content_paragraphs
                    )
                    if not ok:
                        raise _IncompleteContentError(reason)
                success = True
                _emit(
                    "PROGRESS",
                    f"    requests 成功（{len(article.content_paragraphs)} 段）",
                )
                break
            except Exception as e:
                last_error = e
                short = str(e)[:180]
                _emit("PROGRESS", f"    requests 失败: {short}")

        if not success and PLAYWRIGHT_FALLBACK_ENABLED:
            _emit("PROGRESS", "    requests 全部失败，启动 Playwright 兜底")
            try:
                html = self._fetch_html_via_playwright(article.url)
                self._populate_article_from_html(article, html)
                if require_full_content:
                    ok, reason = self._is_full_text_complete(
                        html, article.content_paragraphs
                    )
                    if not ok:
                        raise _IncompleteContentError(reason)
                success = True
                _emit(
                    "PROGRESS",
                    f"    Playwright 成功: {len(article.content_paragraphs)} 段",
                )
            except Exception as e:
                last_error = e

        time.sleep(REQUEST_DELAY_SECONDS)
        if not success:
            raise RuntimeError(str(last_error))
        return article

    def _fetch_html_via_requests(self, url: str) -> str:
        resp = self._session.get(
            url,
            timeout=(ARTICLE_FETCH_TIMEOUT_CONNECT, ARTICLE_FETCH_TIMEOUT_READ),
            allow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text

    # ── Playwright 兜底 ──────────────────────────────────

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
            "locale": "zh-CN",
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
                page.goto(
                    current_url,
                    wait_until="networkidle",
                    timeout=PLAYWRIGHT_TIMEOUT_MS,
                )
                self._scroll_until_stable(page)
                html = page.content()
                html_parts.append(html)

                next_url = self.discover_next_page_from_html(
                    html, current_url, visited
                )
                if next_url and page_index + 1 < PAGINATION_MAX_PAGES:
                    _emit("PROGRESS", f"    发现下一页，继续抓取: {next_url}")
                current_url = next_url
            finally:
                page.close()

        if not html_parts:
            raise RuntimeError("Playwright 未抓取到页面内容")

        return "\n<!-- SC_PAGE_BREAK -->\n".join(html_parts)

    def _scroll_until_stable(self, page) -> None:
        stable_rounds = 0
        prev_signal = (-1, -1)

        while stable_rounds < CONTENT_STABLE_ROUNDS:
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                return
            page.wait_for_timeout(1000)
            current_signal = self._content_signal(page)
            if current_signal == prev_signal:
                stable_rounds += 1
            else:
                stable_rounds = 0
            prev_signal = current_signal

    def _content_signal(self, page) -> tuple[int, int]:
        try:
            signal = page.evaluate(
                """
                () => {
                  const container = document.querySelector('article, main, .entry-content, .post-content') || document.body;
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
            if (
                "page=" in href
                or "/page/" in href
                or text in {"next", "next page", "下一页", "下一页 »"}
            ):
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
            for sel in ("h1.entry-title", "h1.post-title", "h1"):
                h1 = soup.select_one(sel)
                if h1:
                    article.title = h1.get_text(strip=True)
                    if article.title:
                        break

        if not article.summary:
            meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
                "meta", attrs={"property": "og:description"}
            )
            if meta and meta.get("content"):
                article.summary = meta["content"].strip()

        if not article.date:
            for sel in (
                'meta[property="article:published_time"]',
                'meta[property="og:article:published_time"]',
                'meta[itemprop="datePublished"]',
                "time[datetime]",
            ):
                node = soup.select_one(sel)
                if not node:
                    continue
                raw = node.get("content") or node.get("datetime") or node.get_text(strip=True)
                if not raw:
                    continue
                article.date = _extract_date(raw) or raw[:10]
                if article.date:
                    break
            if not article.date:
                article.date = _extract_date(soup.get_text(" ", strip=True)) or ""

        paragraphs: list[str] = []
        seen: set[str] = set()

        container_selectors = (
            ".entry-content",
            ".post-content",
            "article .content",
            "article",
            "main",
        )

        for sel in container_selectors:
            container = soup.select_one(sel)
            if not container:
                continue
            for p in container.find_all("p"):
                text = " ".join(p.get_text(" ", strip=True).split())
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
                text = " ".join(p.get_text(" ", strip=True).split())
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
        del html
        if len(paragraphs) < FULLTEXT_MIN_PARAGRAPHS:
            return False, f"正文段落不足（{len(paragraphs)} < {FULLTEXT_MIN_PARAGRAPHS}）"

        total_chars = sum(len(p) for p in paragraphs)
        if total_chars < FULLTEXT_MIN_PARAGRAPHS * 80:
            return False, "正文总长度不足，疑似仅摘要"

        return True, "ok"

    # ── 清理 ─────────────────────────────────────────────

    def _reset_runtime_clients(self) -> None:
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


def _extract_date(text: str) -> str:
    if not text:
        return ""
    match = _DATE_RE.search(text)
    if not match:
        return ""
    y, m, d = match.groups()
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
