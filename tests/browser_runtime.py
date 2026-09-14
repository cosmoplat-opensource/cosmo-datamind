"""Bounded browser cleanup for standalone acceptance scripts."""
import asyncio
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright


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
