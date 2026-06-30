"""
Stealth browser helper — wraps Playwright with anti-detection measures
to bypass Akamai Bot Manager on NSE.
"""

import asyncio
import json
import os
import random

from playwright.async_api import async_playwright, BrowserContext, Page
from playwright_stealth import Stealth
import logging

logger = logging.getLogger(__name__)

COOKIE_PATH = os.path.join(os.path.dirname(__file__), ".nse_cookies.json")

_stealth = Stealth(
    chrome_runtime=True,
    navigator_webdriver=True,
    navigator_platform_override="macOS",
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]


async def random_delay(min_s: float = 2.0, max_s: float = 6.0):
    """Sleep for a random duration to mimic human timing."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_mouse_move(page: Page):
    """Simulate a small random mouse movement."""
    x = random.randint(200, 800)
    y = random.randint(200, 600)
    await page.mouse.move(x, y, steps=random.randint(5, 15))


async def create_stealth_context(playwright) -> tuple:
    """
    Launch a Chrome browser with stealth settings and return (browser, context).
    Uses channel='chrome' for new headless mode, applies stealth patches,
    and loads persisted cookies if available.
    """
    ua = random.choice(USER_AGENTS)

    browser = await playwright.chromium.launch(
        headless=True,
        # Use chromium instead of chrome channel (chrome not available in Docker)
    )

    context_args = {
        "user_agent": ua,
        "viewport": {"width": 1920, "height": 1080},
        "locale": "en-IN",
        "timezone_id": "Asia/Kolkata",
        "extra_http_headers": {
            "Accept-Language": "en-IN,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
        },
    }

    # Load persisted cookies / storage state if available
    if os.path.exists(COOKIE_PATH):
        try:
            context_args["storage_state"] = COOKIE_PATH
        except Exception:
            pass

    ctx = await browser.new_context(**context_args)
    return browser, ctx


async def apply_stealth(page: Page):
    """Apply stealth patches and anti-detection init scripts to a page."""
    await _stealth.apply_stealth_async(page)


async def save_cookies(ctx: BrowserContext):
    """Persist browser storage state (cookies, localStorage) for next run."""
    try:
        await ctx.storage_state(path=COOKIE_PATH)
    except Exception as e:
        logger.warning(f"  Warning: could not save cookies: {e}")


def get_cookies_for_httpx(ctx_cookies: list[dict]) -> dict[str, str]:
    """Convert Playwright cookies to a simple dict for httpx."""
    return {c["name"]: c["value"] for c in ctx_cookies if "nseindia" in c.get("domain", "")}
