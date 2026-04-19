#!/usr/bin/env python3
"""
sc-article-scrap 主入口
按分类页抓取 mckinsey.com.cn 上 AI / 汽车 / 创新相关报告，
将标题存入飞书多维表格，将完整内容存入飞书文档。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from config import (
    TOPIC_CATEGORY_PATHS,
    TOPIC_LABELS,
    DEFAULT_LIMIT_PER_TOPIC,
    validate_config,
)
from feishu_client import FeishuClient
from run_state import RunStateManager
from scraper import Article, McKinseyScraper


def p(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def _article_sort_key(article: Article) -> tuple[str, str]:
    return (article.date or "", article.url)


def _select_global_daily_articles(
    candidates: list[tuple[str, Article]],
    daily_total_limit: int,
) -> tuple[list[tuple[str, Article]], int]:
    best_by_url: dict[str, tuple[str, Article]] = {}
    for topic, article in candidates:
        existing = best_by_url.get(article.url)
        if not existing:
            best_by_url[article.url] = (topic, article)
            continue
        _, prev = existing
        if _article_sort_key(article) > _article_sort_key(prev):
            best_by_url[article.url] = (topic, article)

    merged = list(best_by_url.values())
    merged.sort(key=lambda x: _article_sort_key(x[1]), reverse=True)

    if daily_total_limit <= 0:
        return merged, 0

    selected = merged[:daily_total_limit]
    dropped = max(0, len(merged) - len(selected))
    return selected, dropped


def build_feishu_doc_blocks(article: Article) -> list[dict]:
    blocks = []

    meta_lines = []
    if article.date:
        meta_lines.append(f"发布日期: {article.date}")
    if article.authors:
        meta_lines.append(f"作者: {article.authors}")
    meta_lines.append(f"来源分类: {TOPIC_LABELS.get(article.topic, article.topic)}")
    meta_lines.append(f"原文链接: {article.url}")

    for line in meta_lines:
        blocks.append(FeishuClient.make_text_block(line))

    if article.summary:
        blocks.append(FeishuClient.make_text_block("摘要"))
        blocks.append(FeishuClient.make_text_block(article.summary))

    if article.content_paragraphs:
        blocks.append(FeishuClient.make_text_block("正文"))
        for para in article.content_paragraphs:
            blocks.append(FeishuClient.make_text_block(para))
    elif article.summary:
        blocks.append(FeishuClient.make_text_block("内容概要"))
        blocks.append(FeishuClient.make_text_block(article.summary))

    return blocks


def build_bitable_fields(article: Article, doc_url: str) -> dict:
    """构造多维表格记录字段"""
    now_ms = int(time.time() * 1000)
    date_ms = None
    if article.date:
        try:
            dt = datetime.strptime(article.date.strip()[:10], "%Y-%m-%d")
            date_ms = int(dt.timestamp() * 1000)
        except ValueError:
            pass

    fields = {
        "标题": article.title,
        "链接": {"text": article.title, "link": article.url},
        "主题分类": TOPIC_LABELS.get(article.topic, article.topic),
        "摘要": (article.summary[:500] if article.summary else ""),
        "飞书文档链接": {"text": article.title, "link": doc_url},
        "爬取时间": now_ms,
    }
    if date_ms:
        fields["发布日期"] = date_ms
    if article.authors:
        fields["作者"] = article.authors

    return fields


def run(
    topics: list[str],
    limit: int,
    fetch_content: bool = True,
    daily_total_limit: int = 3,
    require_full_content: bool = True,
):
    start_time = time.time()
    total_phases = 2 + len(topics)  # 连接 + N 个主题 + 处理
    current_phase = 0

    missing = validate_config()
    if missing:
        p("FAIL", f"缺少飞书配置项: {', '.join(missing)}")
        p("FAIL", "请在 .env 文件或环境变量中配置，参考 .env.example")
        sys.exit(1)

    # ── Phase 1: 连接飞书 & 加载去重数据 ──
    current_phase += 1
    p("PHASE", f"({current_phase}/{total_phases}) 连接飞书，加载已有数据用于去重...")

    feishu = FeishuClient()
    scraper = McKinseyScraper()
    run_state = RunStateManager()

    try:
        try:
            existing_urls = feishu.get_existing_urls()
            p("OK", f"飞书连接成功，多维表格中已有 {len(existing_urls)} 条记录")
        except Exception as e:
            p("FAIL", f"读取已有记录失败，将跳过去重: {e}")
            existing_urls = set()

        return _do_run(
            scraper=scraper,
            feishu=feishu,
            run_state=run_state,
            topics=topics,
            limit=limit,
            fetch_content=fetch_content,
            require_full_content=require_full_content,
            daily_total_limit=daily_total_limit,
            existing_urls=existing_urls,
            start_time=start_time,
            total_phases=total_phases,
            current_phase=current_phase,
        )
    finally:
        try:
            scraper.close()
        except Exception as e:
            p("FAIL", f"scraper 资源清理失败: {e}")


def _do_run(
    *,
    scraper,
    feishu,
    run_state,
    topics,
    limit,
    fetch_content,
    require_full_content,
    daily_total_limit,
    existing_urls,
    start_time,
    total_phases,
    current_phase,
):
    stats = {
        "scraped": 0,
        "bitable_ok": 0,
        "doc_ok": 0,
        "skipped": 0,
        "errors": [],
    }
    topic_stats = {}

    candidate_articles: list[tuple[str, Article]] = []

    for topic in topics:
        current_phase += 1
        category_path = TOPIC_CATEGORY_PATHS.get(topic, "")
        label = TOPIC_LABELS.get(topic, topic)
        p("PHASE", f"({current_phase}/{total_phases}) 抓取主题: {label}")
        p("PROGRESS", f"分类页: {category_path}")

        topic_stats[topic] = {
            "searched": 0,
            "skipped": 0,
            "new": 0,
            "ok": 0,
            "fail": 0,
        }

        fetch_limit = max(limit * 3, daily_total_limit * 3)
        articles = scraper.search_category(category_path, limit=fetch_limit)
        topic_stats[topic]["searched"] = len(articles)

        if not articles:
            p("PROGRESS", f"主题 [{label}] 未搜索到任何文章")
            continue

        p("PROGRESS", f"搜索到 {len(articles)} 篇，正在去重...")

        new_articles = []
        for a in articles:
            if a.url in existing_urls:
                topic_stats[topic]["skipped"] += 1
                stats["skipped"] += 1
                p("SKIP", f"{a.title[:60]}")
                continue
            new_articles.append(a)
            if len(new_articles) >= limit:
                break

        topic_stats[topic]["new"] = len(new_articles)
        p(
            "PROGRESS",
            f"主题 [{label}] 结果: 搜索 {len(articles)} 篇 → "
            f"跳过 {topic_stats[topic]['skipped']} 篇 → "
            f"候选新增 {len(new_articles)} 篇",
        )

        for a in new_articles:
            a.topic = topic
            candidate_articles.append((topic, a))

    selected_articles, dropped_count = _select_global_daily_articles(
        candidate_articles,
        daily_total_limit,
    )
    if dropped_count:
        p(
            "PROGRESS",
            f"全局限额生效: 候选 {len(candidate_articles)} 篇，保留最新 {len(selected_articles)} 篇，舍弃 {dropped_count} 篇",
        )

    current_phase += 1
    total_articles = len(selected_articles)

    if total_articles == 0:
        p("PHASE", f"({current_phase}/{total_phases}) 无新文章需要处理")
    else:
        p("PHASE", f"({current_phase}/{total_phases}) 开始处理 {total_articles} 篇新文章...")

        for idx, (topic, article) in enumerate(selected_articles, 1):
            label = TOPIC_LABELS.get(topic, topic)
            title_short = article.title[:55]
            p("ARTICLE", f"({idx}/{total_articles}) [{label}] {title_short}")

            step_status = {"抓取": "⏳", "飞书文档": "⏳", "多维表格": "⏳"}
            hard_fail = False

            try:
                if fetch_content:
                    scraper.fetch_article_content(
                        article,
                        require_full_content=require_full_content,
                    )
                    para_count = len(article.content_paragraphs)
                    step_status["抓取"] = f"✅ {para_count}段"
                else:
                    step_status["抓取"] = "✅ 跳过(仅摘要模式)"
                stats["scraped"] += 1
            except Exception as e:
                retry_count = run_state.record_failure(article.url)
                step_status["抓取"] = f"❌ {e}"
                stats["errors"].append(f"[{title_short}] 抓取失败: {e}")
                topic_stats[topic]["fail"] += 1
                p("FAIL", f"  抓取失败(重试计数 {retry_count}): {e}")
                stats["scraped"] += 1
                if require_full_content:
                    hard_fail = True
                    step_status["飞书文档"] = "-"
                    step_status["多维表格"] = "-"
                    p("FAIL", "  必须全文模式：该文章不入库")

            if hard_fail:
                p(
                    "FAIL",
                    f"  抓取={step_status['抓取']}  文档={step_status['飞书文档']}  表格={step_status['多维表格']}",
                )
                continue

            doc_url = ""
            try:
                doc_id, doc_url = feishu.create_document(article.title)
                blocks = build_feishu_doc_blocks(article)
                if blocks:
                    feishu.write_document_content(doc_id, blocks)
                step_status["飞书文档"] = "✅"
                stats["doc_ok"] += 1
            except Exception as e:
                step_status["飞书文档"] = f"❌ {e}"
                stats["errors"].append(f"[{title_short}] 飞书文档: {e}")

            try:
                fields = build_bitable_fields(article, doc_url)
                feishu.add_bitable_record(fields)
                step_status["多维表格"] = "✅"
                stats["bitable_ok"] += 1
                existing_urls.add(article.url)
                run_state.record_success(article.url)
            except Exception as e:
                step_status["多维表格"] = f"❌ {e}"
                stats["errors"].append(f"[{title_short}] 多维表格: {e}")
                run_state.record_failure(article.url)

            if "❌" in step_status["飞书文档"] or "❌" in step_status["多维表格"]:
                topic_stats[topic]["fail"] += 1
            else:
                topic_stats[topic]["ok"] += 1

            p(
                "OK" if "❌" not in str(step_status) else "FAIL",
                f"  抓取={step_status['抓取']}  文档={step_status['飞书文档']}  表格={step_status['多维表格']}",
            )

    elapsed = time.time() - start_time
    elapsed_str = f"{int(elapsed // 60)}分{int(elapsed % 60)}秒" if elapsed >= 60 else f"{elapsed:.1f}秒"

    p("REPORT", "=" * 50)
    p("REPORT", f"执行完成，耗时 {elapsed_str}")
    p("REPORT", "-" * 50)

    for topic in topics:
        label = TOPIC_LABELS.get(topic, topic)
        ts = topic_stats.get(topic, {})
        p(
            "REPORT",
            f"  [{label}] 搜索 {ts.get('searched', 0)} 篇 | "
            f"跳过 {ts.get('skipped', 0)} | "
            f"候选新增 {ts.get('new', 0)} | "
            f"成功 {ts.get('ok', 0)} | "
            f"失败 {ts.get('fail', 0)}",
        )

    p("REPORT", "-" * 50)
    p(
        "REPORT",
        f"  全局上限: {daily_total_limit} | 抓取 {stats['scraped']} | 跳过 {stats['skipped']} | 文档 {stats['doc_ok']} | 表格 {stats['bitable_ok']}",
    )

    if stats["errors"]:
        p("REPORT", f"  失败明细 ({len(stats['errors'])} 项):")
        for err in stats["errors"]:
            p("REPORT", f"    - {err}")

    p("REPORT", "=" * 50)
    return stats


def main():
    parser = argparse.ArgumentParser(description="sc-article-scrap: 麦肯锡报告爬取 → 飞书存储")
    parser.add_argument(
        "--topic",
        choices=["ai", "automotive", "design", "all"],
        default="all",
        help="要爬取的主题 (默认: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT_PER_TOPIC,
        help=f"每主题候选新增篇数上限 (默认: {DEFAULT_LIMIT_PER_TOPIC})",
    )
    parser.add_argument(
        "--daily-total-limit",
        type=int,
        default=3,
        help="全局每日新增上限（跨主题聚合后取最新，默认: 3）",
    )
    parser.add_argument(
        "--no-content",
        action="store_true",
        help="不抓取正文（仅列表页摘要）",
    )
    parser.add_argument(
        "--require-full-content",
        dest="require_full_content",
        action="store_true",
        default=True,
        help="抓取正文时要求必须全文（默认开启）",
    )
    parser.add_argument(
        "--allow-summary-fallback",
        dest="require_full_content",
        action="store_false",
        help="抓不到全文时允许摘要兜底入库",
    )

    args = parser.parse_args()

    topics = list(TOPIC_CATEGORY_PATHS.keys()) if args.topic == "all" else [args.topic]

    run(
        topics=topics,
        limit=args.limit,
        fetch_content=not args.no_content,
        daily_total_limit=args.daily_total_limit,
        require_full_content=args.require_full_content,
    )


if __name__ == "__main__":
    main()
