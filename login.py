"""
一次性登入腳本：用 Playwright 開瀏覽器，手動登入 Threads，把 session 存成 cookies.json。
cookies.json 過期後重跑這支腳本即可更新。

需求（只需本機安裝，不需進 Docker）：
  pip install playwright
  playwright install chromium
"""
import asyncio
import os
from playwright.async_api import async_playwright

COOKIES_PATH = os.path.join(os.path.dirname(__file__), "cookies.json")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/137.0.0.0 Safari/537.36"
            ),
            locale="zh-TW",
        )
        page = await context.new_page()
        await page.goto("https://www.threads.com/login")

        print("請在瀏覽器視窗中完成登入，登入後回到這裡按 Enter...")
        input()

        await context.storage_state(path=COOKIES_PATH)
        await browser.close()
        print(f"已儲存 session 至 {COOKIES_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
