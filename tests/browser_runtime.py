"""Bounded browser cleanup for standalone acceptance scripts."""
import asyncio
import os
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright


# 本机浏览器的候选位置:优先环境变量,其次 macOS 与 Linux 的常见安装位置;都没有时由 Playwright 用自带的 Chromium
BROWSER_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
)


def browser_executable():
    """第一个存在的本机浏览器路径;返回 None 时交给 Playwright 自带的 Chromium。"""
    for path in (os.environ.get("DATAMIND_BROWSER_EXECUTABLE"), *BROWSER_CANDIDATES):
        if path and os.path.isfile(path):
            return path
    return None


@asynccontextmanager
async def managed_playwright():
    runtime = await async_playwright().start()
    try:
        yield runtime
    finally:
        await asyncio.wait_for(runtime.stop(), timeout=15)


async def close_browser(browser, timeout=15):
    async def close():
        # Close owned contexts explicitly before the browser/driver handshake.
        for context in browser.contexts:
            await context.close()
        await browser.close()

    print("  浏览器检查完成，正在关闭测试上下文…", flush=True)
    await asyncio.wait_for(close(), timeout=timeout)
