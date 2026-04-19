---
name: sc-article-scrap
description: 从麦肯锡中国（mckinsey.com.cn）分类页爬取 AI、汽车、创新相关报告，按 URL 去重后将标题存入飞书多维表格，完整内容存入飞书文档
user-invocable: true
metadata:
  openclaw:
    emoji: "📰"
    os: ["darwin", "linux", "win32"]
    requires:
      bins: ["python3"]
      env: ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BITABLE_APP_TOKEN", "FEISHU_BITABLE_TABLE_ID", "FEISHU_FOLDER_TOKEN"]
    primaryEnv: "FEISHU_APP_ID"
---

# 麦肯锡中国报告爬取 → 飞书存储

按分类页抓取 mckinsey.com.cn 上最新的 AI（数字化）、汽车、创新主题报告，按 URL 去重（跳过已存在的文章），将标题存入飞书多维表格，将完整内容存入同文件夹下的飞书文档。

## 用户输入

用户可通过 `/sc-article-scrap` 传入参数：$ARGUMENTS

支持的参数：
- `--topic ai|automotive|design|all` — 主题筛选（默认 all）
- `--limit N` — 每个主题候选新增上限（默认 5，已存在的不计入）
- `--daily-total-limit N` — 全局每日新增上限（跨主题聚合后取最新，默认 3）
- `--no-content` — 仅存列表页摘要，不抓取正文（更快）
- `--require-full-content` — 必须抓到全文才入库（默认开启）
- `--allow-summary-fallback` — 抓不到全文时允许摘要兜底入库

示例：
- `/sc-article-scrap` → 全部主题，跨主题全局最多新增 3 篇（默认）
- `/sc-article-scrap --topic ai --limit 3` → 仅 AI 主题，新增 3 篇
- `/sc-article-scrap --topic all --daily-total-limit 3 --require-full-content` → 每日模式（推荐）

## 主题 → 分类页映射

| 主题 | 分类页 |
|------|--------|
| `ai` | `/insights/business-technology/`（数字化） |
| `automotive` | `/insights/autos/`（汽车） |
| `design` | `/insights/innovation/`（创新） |

## 重要：实时进度展示

**你必须在执行过程中持续向用户展示进度。** 不要等全部完成后才一次性报告。脚本会输出带有特殊前缀的进度行，你需要逐条转述给用户：

- `[PHASE]` — 大阶段切换，用醒目格式展示
- `[PROGRESS]` — 步骤进度，直接展示
- `[ARTICLE]` — 单篇文章处理状态，直接展示
- `[SKIP]` — 去重跳过的文章，直接展示
- `[OK]` — 某项操作成功
- `[FAIL]` — 某项操作失败
- `[REPORT]` — 最终汇总

**展示方式：** 每当脚本输出新的进度行，立刻向用户转述。使用表格或列表格式让进度一目了然。示例：

> **阶段 1/4：连接飞书**
> - 已加载 23 条已有记录用于去重
>
> **阶段 2/4：抓取主题 — AI**
> - 分类页：/insights/business-technology/
> - 列表页解析 12 篇，去重跳过 3 篇，候选新增 5 篇
>
> **阶段 3/4：处理文章**
> | # | 标题 | 抓取 | 飞书文档 | 多维表格 |
> |---|------|------|---------|---------|
> | 1 | 2026麦肯锡全球技术议程 | ✅ | ✅ | ✅ |
> | 2 | AI悖论——热情高涨 | ✅ | ✅ | ✅ |
> | 3 | 某某文章 | ✅ | ❌ 权限不足 | - |
>
> **阶段 4/4：完成**
> - 新增 2 篇 / 跳过 3 篇 / 失败 1 篇

## 执行步骤

### Step 1: 安装依赖

先告知用户：**"正在检查 Python 依赖..."**

```bash
cd {baseDir}/scripts
python3 -c "import requests, bs4, dotenv" 2>/dev/null || pip3 install -r requirements.txt
# Playwright 兜底所需的 Chromium（首次 ~150MB，已安装则秒过）
python3 -m playwright install chromium 2>/dev/null || true
```

mckinsey.com.cn 为 WordPress 站，无反爬墙，默认用 `requests` 抓取；Playwright 仅在 `requests` 全部失败时兜底。

完成后告知用户依赖就绪。

### Step 2: 环境变量检查

先告知用户：**"正在检查飞书 API 配置..."**

检查以下环境变量：

| 变量名 | 说明 |
|--------|------|
| `FEISHU_APP_ID` | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 飞书应用 App Secret |
| `FEISHU_BITABLE_APP_TOKEN` | 多维表格 token |
| `FEISHU_BITABLE_TABLE_ID` | 表格 ID |
| `FEISHU_FOLDER_TOKEN` | 文档存放文件夹 |

如缺少变量，停止执行并提示用户配置，参考 `{baseDir}/scripts/.env.example`。
配置完整则告知用户：**"配置检查通过"**

### Step 3: 执行爬取（核心步骤）

告知用户：**"开始执行爬取，以下是实时进度..."**

```bash
cd {baseDir}/scripts
python3 -u main.py $ARGUMENTS
```

**注意 `-u` 参数：** 强制 Python 不缓冲输出，确保进度行实时可见。

默认推荐每日低频稳定模式：

```bash
cd {baseDir}/scripts
python3 -u main.py --topic all --daily-total-limit 3 --require-full-content
```

说明：
- 每天跨主题最多入库 3 篇最新新文章
- 正文未通过"全文判定"时不入库，避免"只抓一页"伪成功

脚本运行期间，持续读取输出并向用户实时展示进度（见上方「进度展示」要求）。

### Step 4: 最终报告

脚本输出 `[REPORT]` 行后，将汇总整理为用户友好的最终报告，包含：
- 总耗时
- 各主题新增/跳过/失败数量
- 如有失败，列出具体原因和建议

## 去重机制

每次运行前从飞书多维表格读取已有「链接」字段 URL，列表页解析到的文章如 URL 已存在则跳过。`--limit` 控制**新增**数量。

## 飞书多维表格字段要求

| 字段名 | 类型 | 说明 |
|--------|------|------|
| 标题 | 文本 | 文章标题 |
| 链接 | 超链接 | 麦肯锡原文链接（去重依据） |
| 主题分类 | 单选 | AI / 汽车 / 创新 |
| 发布日期 | 日期 | 文章发布日期 |
| 摘要 | 文本 | 文章摘要 |
| 作者 | 文本 | 文章作者 |
| 飞书文档链接 | 超链接 | 对应的飞书文档链接 |
| 爬取时间 | 日期 | 数据爬取时间 |
