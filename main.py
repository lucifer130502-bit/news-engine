import asyncio
import logging
import os
import signal
from datetime import datetime, timezone, timedelta
from aiohttp import web

import httpx

from config.config_context import config
from logs.logging_config import setup_logging
from scraper.crawler_engine import run_crawl_cycle

# Initialize logging once at startup
setup_logging()
logger = logging.getLogger(__name__)

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

INTERVAL = config.app.crawl_interval_seconds
PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")

logger.info("Profile: %s", config.app_profile)
logger.info("Crawl interval: %ds", INTERVAL)
logger.info("LLM model: %s", config.app.llm_model)

_shutdown = asyncio.Event()


# ── Health check endpoint ────────────────────────────────────────────────────

async def _health(request):
    return web.json_response({"status": "ok", "timestamp": datetime.now(IST).isoformat()})


async def _start_health_server() -> None:
    """Start a lightweight HTTP server for Render health checks."""
    app = web.Application()
    app.router.add_get("/health", _health)
    app.router.add_get("/", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Health server started on port %d", PORT)


async def _self_ping() -> None:
    """Ping own health endpoint every 4 minutes to prevent Render free tier sleep."""
    if not RENDER_URL:
        logger.info("RENDER_EXTERNAL_URL not set — self-ping disabled")
        return
    url = f"{RENDER_URL}/health"
    logger.info("Self-ping enabled — hitting %s every 4 minutes", url)
    while not _shutdown.is_set():
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=240)
            break
        except asyncio.TimeoutError:
            pass
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                logger.debug("Self-ping: %s", resp.status_code)
        except Exception as e:
            logger.warning("Self-ping failed: %s", e)


# ── Core loops ───────────────────────────────────────────────────────────────

async def _truncate_at_midnight() -> None:
    """Truncate the announcements table every day at 00:01 IST."""
    while not _shutdown.is_set():
        now = datetime.now(IST)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
        if now.hour == 0 and now.minute < 1:
            tomorrow = now.replace(hour=0, minute=1, second=0, microsecond=0)
        wait_seconds = (tomorrow - now).total_seconds()
        logger.info("Next truncate scheduled in %.0f seconds (at %s IST)", wait_seconds, tomorrow.strftime("%Y-%m-%d %H:%M"))
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=wait_seconds)
            break
        except asyncio.TimeoutError:
            pass

        try:
            import database.db as db
            db.truncate_announcements()
            logger.info("Truncated announcements table at 00:01 IST")
        except Exception:
            logger.exception("Failed to truncate announcements table")


async def _crawler_loop() -> None:
    logger.info("Crawler started — interval=%ds", INTERVAL)
    while not _shutdown.is_set():
        try:
            await run_crawl_cycle()
        except Exception:
            logger.exception("Crawl cycle failed")
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=INTERVAL)
        except asyncio.TimeoutError:
            pass
    logger.info("Crawler stopped gracefully")


def _handle_signal(sig: signal.Signals) -> None:
    logger.info("Received %s — shutting down", sig.name)
    _shutdown.set()


async def main() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    await _start_health_server()

    tasks = [
        asyncio.create_task(_crawler_loop()),
        asyncio.create_task(_truncate_at_midnight()),
        asyncio.create_task(_self_ping()),
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
