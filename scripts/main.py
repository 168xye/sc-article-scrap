#!/usr/bin/env python3
"""
sc-article-scrap 主入口
通过麦肯锡搜索 API 获取 AI、汽车、工业设计相关报告，
将标题存入飞书多维表格，将完整内容存入飞书文档。

输出带有结构化前缀的进度行，供 OpenClaw agent 实时转述给用户：
  [PHASE]    大阶段切换
  [PROGRESS] 步骤进度
  [ARTICLE]  单篇文章处理状态
  [SKIP]     去重跳过
  [OK]       操作成功
  [FAIL]     操作失败
  [REPORT]   最终汇总
"""

import argparse
import sys
import time
from datetime import datetime

from config import (
    TOPIC_KEYWORDS,
    TOPIC_LABELS,
    DEFAULT_LIMIT_PER_TOPIC,
    validate_config,
)
from scraper import McKinseyScraper, Article
from feishu_client import FeishuClient


def p(tag: str, msg: str) -> None:
    """打印带前缀的进度行，立即刷新"""
    print(f"[{tag}] {msg}", flush=True)


def build_feishu_doc_blocks(article: Article) -> list[dict]:
    """将文章内容转换为飞书文档 Block 列表"""
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


def run(topics: list[str], limit: int, fetch_content: bool = True):
    start_time = time.time()
    total_phases = 3 + len(topics)  # 连接 + N个主题搜索 + 处理 + 汇总
    current_phase = 0

    # ── 配置校验 ──
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
            topics=topics,
            limit=limit,
            fetch_content=fetch_content,
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
    topics,
    limit,
    fetch_content,
    existing_urls,
    start_time,
    total_phases,
    current_phase,
):
    # ── 统计 ──
    stats = {
        "scraped": 0,
        "bitable_ok": 0,
        "doc_ok": 0,
        "skipped": 0,
        "errors": [],
    }
    topic_stats = {}  # topic -> {searched, skipped, new, ok, fail}

    # ── Phase 2~N: 逐主题搜索 & 处理 ──
    all_new_articles: list[tuple[str, Article]] = []  # (topic, article)

    for topic in topics:
        current_phase += 1
        keywords = TOPIC_KEYWORDS.get(topic, [])
        label = TOPIC_LABELS.get(topic, topic)
        p("PHASE", f"({current_phase}/{total_phases}) 搜索主题: {label}")
        p("PROGRESS", f"关键词: {', '.join(keywords)}")

        topic_stats[topic] = {
            "searched": 0,
            "skipped": 0,
            "new": 0,
            "ok": 0,
            "fail": 0,
        }

        # 搜索
        fetch_limit = limit * 3
        articles = scraper.search_topic(keywords, limit=fetch_limit)
        topic_stats[topic]["searched"] = len(articles)

        if not articles:
            p("PROGRESS", f"主题 [{label}] 未搜索到任何文章")
            continue

        p("PROGRESS", f"搜索到 {len(articles)} 篇，正在去重...")

        # 去重
        new_articles = []
        for a in articles:
            if a.url in existing_urls:
                topic_stats[topic]["skipped"] += 1
                stats["skipped"] += 1
                p("SKIP", f"{a.title[:60]}")
            else:
                new_articles.append(a)
            if len(new_articles) >= limit:
                break

        topic_stats[topic]["new"] = len(new_articles)
        p(
            "PROGRESS",
            f"主题 [{label}] 结果: 搜索 {len(articles)} 篇 → "
            f"跳过 {topic_stats[topic]['skipped']} 篇 → "
            f"新增 {len(new_articles)} 篇待处理",
        )

        for a in new_articles:
            a.topic = topic
            all_new_articles.append((topic, a))

    # ── Phase N+1: 逐篇处理 ──
    current_phase += 1
    total_articles = len(all_new_articles)

    if total_articles == 0:
        p("PHASE", f"({current_phase}/{total_phases}) 无新文章需要处理")
    else:
        p(
            "PHASE",
            f"({current_phase}/{total_phases}) 开始处理 {total_articles} 篇新文章...",
        )

        for idx, (topic, article) in enumerate(all_new_articles, 1):
            label = TOPIC_LABELS.get(topic, topic)
            title_short = article.title[:55]
            p("ARTICLE", f"({idx}/{total_articles}) [{label}] {title_short}")

            # 1) 抓取正文
            step_status = {"抓取": "⏳", "飞书文档": "⏳", "多维表格": "⏳"}
            try:
                if fetch_content:
                    scraper.fetch_article_content(article)
                    para_count = len(article.content_paragraphs)
                    step_status["抓取"] = f"✅ {para_count}段" if para_count else "✅ 仅摘要"
                else:
                    step_status["抓取"] = "✅ 跳过(仅摘要模式)"
                stats["scraped"] += 1
            except Exception as e:
                step_status["抓取"] = f"❌ {e}"
                stats["errors"].append(f"[{title_short}] 抓取失败: {e}")
                topic_stats[topic]["fail"] += 1
                p("FAIL", f"  抓取: {e}")
                # 抓取失败仍继续，用 API 摘要
                stats["scraped"] += 1

            # 2) 创建飞书文档
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

            # 3) 写入多维表格
            try:
                fields = build_bitable_fields(article, doc_url)
                feishu.add_bitable_record(fields)
                step_status["多维表格"] = "✅"
                stats["bitable_ok"] += 1
                existing_urls.add(article.url)
            except Exception as e:
                step_status["多维表格"] = f"❌ {e}"
                stats["errors"].append(f"[{title_short}] 多维表格: {e}")

            # 判定整篇成败
            if "❌" in step_status["飞书文档"] or "❌" in step_status["多维表格"]:
                topic_stats[topic]["fail"] += 1
            else:
                topic_stats[topic]["ok"] += 1

            # 输出单篇结果
            p(
                "OK" if "❌" not in str(step_status) else "FAIL",
                f"  抓取={step_status['抓取']}  "
                f"文档={step_status['飞书文档']}  "
                f"表格={step_status['多维表格']}",
            )

    # ── 最终汇总 ──
    elapsed = time.time() - start_time
    elapsed_str = (
        f"{int(elapsed // 60)}分{int(elapsed % 60)}秒"
        if elapsed >= 60
        else f"{elapsed:.1f}秒"
    )

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
            f"新增 {ts.get('new', 0)} | "
            f"成功 {ts.get('ok', 0)} | "
            f"失败 {ts.get('fail', 0)}",
        )

    p("REPORT", "-" * 50)
    p("REPORT", f"  总计: 抓取 {stats['scraped']} | 跳过 {stats['skipped']} | 文档 {stats['doc_ok']} | 表格 {stats['bitable_ok']}")

    if stats["errors"]:
        p("REPORT", f"  失败明细 ({len(stats['errors'])} 项):")
        for err in stats["errors"]:
            p("REPORT", f"    - {err}")

    p("REPORT", "=" * 50)

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="sc-article-scrap: 麦肯锡报告爬取 → 飞书存储"
    )
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
        help=f"每个主题最多新增篇数 (默认: {DEFAULT_LIMIT_PER_TOPIC}，已存在的不计入)",
    )
    parser.add_argument(
        "--no-content",
        action="store_true",
        help="不抓取文章正文（仅用 API 返回的摘要）",
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        help="自定义搜索关键词（覆盖主题默认关键词）",
    )
    args = parser.parse_args()

    if args.keywords:
        TOPIC_KEYWORDS["custom"] = args.keywords
        TOPIC_LABELS["custom"] = "自定义"
        topics = ["custom"]
    else:
        topics = (
            list(TOPIC_KEYWORDS.keys()) if args.topic == "all" else [args.topic]
        )

    run(topics=topics, limit=args.limit, fetch_content=not args.no_content)


if __name__ == "__main__":
    main()
