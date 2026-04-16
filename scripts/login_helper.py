#!/usr/bin/env python3
"""
一次性登录工具：

打开一个有头浏览器窗口（优先使用系统 Chrome 以绕过 Akamai TLS 指纹检测），
让你手动登录麦肯锡账号。完成后回到终端按回车，脚本会把登录状态保存到
`playwright_state.json`，之后运行 main.py 时 curl_cffi 和 Playwright 都会
自动加载这份状态。

使用：
  cd sc-article-scrap/scripts
  python3 login_helper.py

⚠️  playwright_state.json 包含会话 cookies，请勿提交 git 或分享。
    cookies 过期后重跑此脚本覆盖即可。
"""

import sys

from config import (
    MCKINSEY_BASE,
    PLAYWRIGHT_CHANNEL,
    PLAYWRIGHT_LAUNCH_ARGS,
    PLAYWRIGHT_STEALTH_JS,
    PLAYWRIGHT_STORAGE_STATE_PATH,
    PROXY_URL,
)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "[ERROR] Playwright 未安装。请先执行:\n"
            "  pip3 install playwright\n"
            "  python3 -m playwright install chromium",
            file=sys.stderr,
        )
        return 1

    print("=" * 60)
    print("  麦肯锡登录状态保存工具")
    print("=" * 60)
    print()
    print("  1) 回车后会打开浏览器窗口，自动跳到 mckinsey.com")
    print("  2) 在浏览器中完成登录（Sign in 按钮在右上角）")
    print(f"  3) 登录完成后回到此终端，再按一次回车保存状态")
    print(f"  4) 状态文件将写入:")
    print(f"       {PLAYWRIGHT_STORAGE_STATE_PATH}")
    print()
    input("按回车打开浏览器 > ")

    with sync_playwright() as pw:
        # 优先使用系统 Chrome（TLS 指纹真实，不被 Akamai 拦截）
        launch_kwargs = {"headless": False, "args": PLAYWRIGHT_LAUNCH_ARGS}
        if PROXY_URL:
            launch_kwargs["proxy"] = {"server": PROXY_URL}
            print(f"  [INFO] 使用代理: {PROXY_URL}")
        browser = None

        if PLAYWRIGHT_CHANNEL:
            try:
                launch_kwargs["channel"] = PLAYWRIGHT_CHANNEL
                browser = pw.chromium.launch(**launch_kwargs)
                print(f"  [OK] 已使用系统 Chrome (channel={PLAYWRIGHT_CHANNEL})")
            except Exception as e:
                print(f"  [WARN] 系统 Chrome 不可用: {e}")
                print("         回退到 Playwright 自带 Chromium...")
                launch_kwargs.pop("channel", None)

        if browser is None:
            browser = pw.chromium.launch(**launch_kwargs)

        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(PLAYWRIGHT_STEALTH_JS)
        page = ctx.new_page()

        try:
            page.goto(MCKINSEY_BASE, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"  [WARN] 打开首页失败: {e}")
            print("         你仍可在浏览器地址栏手动输入 URL 再登录")

        print()
        print("浏览器已打开。完成登录后回到此终端，按回车保存：")
        input("> ")

        try:
            ctx.storage_state(path=PLAYWRIGHT_STORAGE_STATE_PATH)
        finally:
            browser.close()

    print()
    print(f"✓ 已保存登录状态到 {PLAYWRIGHT_STORAGE_STATE_PATH}")
    print("  现在运行 `python3 -u main.py` 时会自动使用此状态。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
