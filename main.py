import asyncio
import logging
import signal
from datetime import datetime, timezone, timedelta

from config.config_context import config
from logs.logging_config import setup_logging
from scraper.crawler_engine import run_crawl_cycle

# Initialize logging once at startup
setup_logging()
logger = logging.getLogger(__name__)

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

INTERVAL = config.app.crawl_interval_seconds
logger.info("Profile: %s", config.app_profile)
logger.info("Crawl interval: %ds", INTERVAL)
logger.info("LLM model: %s", config.app.llm_model)

_shutdown = asyncio.Event()


async def _truncate_at_midnight() -> None:
    """Truncate the announcements table every day at 00:01 IST."""
    while not _shutdown.is_set():
        now = datetime.now(IST)
        # Calculate next 00:01 IST
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
        if now.hour == 0 and now.minute < 1:
            # If it's before 00:01 today, truncate today
            tomorrow = now.replace(hour=0, minute=1, second=0, microsecond=0)
        wait_seconds = (tomorrow - now).total_seconds()
        logger.info("Next truncate scheduled in %.0f seconds (at %s IST)", wait_seconds, tomorrow.strftime("%Y-%m-%d %H:%M"))
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=wait_seconds)
            break  # shutdown was requested
        except asyncio.TimeoutError:
            pass  # time to truncate

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

    tasks = [
        asyncio.create_task(_crawler_loop()),
        asyncio.create_task(_truncate_at_midnight()),
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
