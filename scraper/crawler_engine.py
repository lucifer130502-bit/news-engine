from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import uuid
from datetime import datetime, timezone, timedelta

import httpx

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

import ai.ai_processor as ai
import scraper.stealth_browser as sb
import notifier.slack_notifier as slack_notifier
import database.db as db
import utils.symbol_master as symbol_master

logger = logging.getLogger(__name__)

# Cap LLM-summary retries. An announcement whose summary keeps coming back empty is left UNSTORED
# (see process_announcement) so a later crawl cycle re-attempts it; this in-memory counter (keyed by
# the stable announcement id) bounds those re-attempts per process lifetime so a permanently-failing
# item is not re-processed forever. Resets on restart.
MAX_SUMMARY_ATTEMPTS = 3
_summary_attempts: dict[str, int] = {}

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bseindia.com/",
}



# ── Date parsing helpers ─────────────────────────────────────────────────────

def parse_nse_date(date_str: str) -> str:
    """
    Parse NSE date formats to ISO format 'YYYY-MM-DD'.
    Supports: '05-Mar-2026 22:53:31', '05-Mar-2026', '2026-03-06T10:55:26.98'
    Returns current date if parsing fails.
    """
    if not date_str or not date_str.strip():
        return str(datetime.now().date())

    date_str = date_str.strip()

    try:
        # New NSE ISO format with milliseconds: '2026-03-06T10:55:26.98'
        if 'T' in date_str:
            # Split off fractional seconds if present
            if '.' in date_str:
                date_str = date_str.split('.')[0]  # '2026-03-06T10:55:26'
            dt = datetime.fromisoformat(date_str)
            return dt.date().isoformat()
    except ValueError:
        pass

    try:
        # Old NSE format: '05-Mar-2026 22:53:31'
        dt = datetime.strptime(date_str, "%d-%b-%Y %H:%M:%S")
        return dt.date().isoformat()
    except ValueError:
        pass

    try:
        # Alternative format without time: '05-Mar-2026'
        dt = datetime.strptime(date_str, "%d-%b-%Y")
        return dt.date().isoformat()
    except ValueError:
        # Fallback to current date
        logger.warning(f"  Warning: Could not parse NSE date '{date_str}', using current date")
        return str(datetime.now().date())


# ── NSE ──────────────────────────────────────────────────────────────────────

async def _download_nse_pdf_via_browser(page, pdf_url: str) -> bytes | None:
    """Download a single NSE PDF using the stealth browser session."""
    if not pdf_url:
        return None
    try:
        resp = await page.request.get(pdf_url, timeout=60000)
        body = await resp.body()
        status = resp.status
        size = len(body)
        logger.debug(f"  NSE PDF (browser): status={status} size={size} start={body[:8]}")
        if status == 200 and size > 500 and body[:4] == b"%PDF":
            return body
        logger.warning(f"  Not a valid PDF for {pdf_url}")
    except Exception as e:
        logger.error(f"  NSE PDF browser download error: {e}")
    return None


async def fetch_nse_announcements() -> list[dict]:
    """
    Download today's NSE announcements via the CSV export button on the
    corporate filings page. Stealth Playwright handles Akamai protection.
    PDFs are also downloaded through the same browser session to bypass Akamai.
    """
    import csv
    import io as _io
    from playwright.async_api import async_playwright
    try:
        async with async_playwright() as p:
            browser, ctx = await sb.create_stealth_context(p)
            page = await ctx.new_page()
            await sb.apply_stealth(page)

            # Warm up — let NSE set Akamai cookies
            await page.goto("https://www.nseindia.com/", wait_until="domcontentloaded")
            await sb.random_delay(3, 6)
            await sb.human_mouse_move(page)

            # Navigate to the announcements page and wait for table to load
            await page.goto(
                "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                wait_until="domcontentloaded",
                timeout=60000,  # Increased timeout for slow NSE website
            )
            await sb.random_delay(3, 5)
            await sb.human_mouse_move(page)

            # Click "Download (.csv)" and save to a stable path before browser closes
            csv_save_path = os.path.join(os.path.dirname(__file__), ".nse_announcements.csv")
            async with page.expect_download(timeout=60000) as dl_info:
                await page.locator("text=Download (.csv)").first.click()
            download = await dl_info.value
            await download.save_as(csv_save_path)

            with open(csv_save_path, "r", encoding="utf-8-sig") as f:
                content = f.read()

            if not content.strip():
                logger.error("NSE: CSV download failed or empty")
                await browser.close()
                return []

            # Normalise header names — NSE occasionally changes capitalisation/spacing
            reader = csv.DictReader(_io.StringIO(content))
            result = []
            for row in reader:
                row = {k.strip().upper(): v.strip() for k, v in row.items()}

                # Log CSV columns once to help debug URL paths
                if not result:
                    logger.debug(f"NSE CSV columns: {list(row.keys())}")

                fname = (
                    row.get("ATTACHMENT NAME")
                    or row.get("ATTACHMENT")
                    or row.get("ATTCHMNTFILE")
                    or ""
                )
                # ATTACHMENT column may contain a full URL or just a filename
                if fname.startswith("http"):
                    pdf_url = fname
                elif fname:
                    pdf_url = f"https://nsearchives.nseindia.com/corporate/{fname}"
                else:
                    pdf_url = ""
                result.append({
                    "company_name": row.get("COMPANY NAME", "Unknown"),
                    "symbol": row.get("SYMBOL", ""),
                    "exchange": "NSE",
                    "announcement_type": row.get("SUBJECT", "Unknown"),
                    "date": parse_nse_date(row.get("BROADCAST DATE/TIME", "")),
                    "details": row.get("DETAILS", ""),
                    "pdf_url": pdf_url,
                })
            logger.info(f"NSE: CSV parsed {len(result)} announcements")

            # Download all NSE PDFs through the stealth browser session (bypasses Akamai)
            logger.info(f"NSE: Downloading PDFs via stealth browser...")
            for item in result:
                pdf_url = item.get("pdf_url", "")
                if pdf_url:
                    pdf_bytes = await _download_nse_pdf_via_browser(page, pdf_url)
                    if pdf_bytes:
                        item["_pdf_bytes"] = pdf_bytes
                    await sb.random_delay(0.5, 1.5)

            downloaded_count = sum(1 for item in result if "_pdf_bytes" in item)
            logger.info(f"NSE: Downloaded {downloaded_count}/{len(result)} PDFs via browser")

            # Save cookies and close browser
            await sb.save_cookies(ctx)
            await browser.close()

            return result
    except Exception as e:
        logger.error(f"NSE fetch error: {e}")
        return []


# ── BSE ──────────────────────────────────────────────────────────────────────

async def fetch_bse_announcements() -> list[dict]:
    # Correct endpoint — returns full text in MORE field, no PDF needed
    url = (
        "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
        "?strCat=-1&strPrevDate=&strScrip=&strSearch=P&strTodt=&strFromdt=&strType=C"
    )
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, headers=BSE_HEADERS)
        logger.info(f"BSE API status: {resp.status_code}")
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
            result = []
            for item in data.get("Table", []):
                fname = item.get("ATTACHMENTNAME", "")
                pdf_url = (
                    f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{fname}"
                    if fname else ""
                )
                # MORE has the full announcement text — use it for AI analysis
                full_text = (item.get("MORE") or item.get("HEADLINE") or "").strip()
                result.append({
                    "company_name": item.get("SLONGNAME", "Unknown"),
                    "symbol": str(item.get("SCRIP_CD", "")),
                    "exchange": "BSE",
                    "announcement_type": item.get("CATEGORYNAME", item.get("SUBCATNAME", "Unknown")),
                    "date": parse_nse_date(item.get("DT_TM", "")),  # BSE uses same date format as NSE
                    "details": full_text,
                    "pdf_url": pdf_url,
                })
            return result
        except Exception as e:
            logger.error(f"BSE JSON parse error: {e} | raw: {resp.text[:300]}")
            return []


# ── PDF download ──────────────────────────────────────────────────────────────

async def download_pdf(url: str, exchange: str) -> bytes | None:
    """Download PDF via httpx. Used for BSE only (NSE PDFs are downloaded via stealth browser)."""
    if not url:
        return None
    ua = random.choice(sb.USER_AGENTS)
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            headers = {
                "User-Agent": ua,
                "Referer": "https://www.bseindia.com/",
                "Accept": "application/pdf,application/octet-stream,*/*",
            }
            resp = await client.get(url, headers=headers)
            ct = resp.headers.get("content-type", "").lower()
            size = len(resp.content)
            logger.debug(f"  PDF: status={resp.status_code} ct='{ct}' size={size} start={resp.content[:8]}")
            if resp.status_code == 200 and size > 500 and resp.content[:4] == b"%PDF":
                return resp.content
            logger.warning(f"  Not a valid PDF for {url}")
    except Exception as e:
        logger.error(f"  PDF download error: {e}")
    return None


# ── Deduplication helpers ────────────────────────────────────────────────────

def generate_dedup_key(announcement: dict) -> str:
    """
    Generate a deduplication key based on metadata (company + date + announcement_type).
    Used to identify cross-exchange duplicates before downloading PDFs.
    """
    company = announcement.get("company_name", "").strip().upper()
    date = announcement.get("date", "").strip()
    ann_type = announcement.get("announcement_type", "").strip().upper()
    # Normalize date to YYYY-MM-DD format
    try:
        date_obj = datetime.fromisoformat(date).date()
        date = date_obj.isoformat()
    except:
        pass
    return f"{company}|{date}|{ann_type}"


def deduplicate_announcements(nse_items: list[dict], bse_items: list[dict]) -> list[dict]:
    """
    Remove cross-exchange duplicates BEFORE processing.
    Prefer NSE over BSE when the same announcement appears in both exchanges.
    Returns: deduplicated list of announcements to process.
    """
    seen_keys = {}
    result = []

    # Process NSE first (preferred)
    for item in nse_items:
        key = generate_dedup_key(item)
        if key not in seen_keys:
            seen_keys[key] = item
            result.append(item)

    # Process BSE, skip if already seen from NSE
    for item in bse_items:
        key = generate_dedup_key(item)
        if key not in seen_keys:
            seen_keys[key] = item
            result.append(item)
        else:
            # Log skipped BSE duplicate
            company = item.get("company_name", "Unknown")
            logger.warning(f"  Skipping BSE duplicate (already in NSE): {company}")

    return result


# ── Per-announcement processing ───────────────────────────────────────────────

async def process_announcement(announcement: dict, custom_instruction: str | None = None, cycle_id: str | None = None, labels: list | None = None, label_priority: list | None = None):
    company = announcement.get("company_name", "Unknown")
    exchange = announcement.get("exchange", "")
    pdf_url = announcement.get("pdf_url", "")
    ann_type = announcement.get("announcement_type", "Unknown")
    date = announcement.get("date", str(datetime.now().date()))
    details = announcement.get("details", "")

    logger.info(f"Processing: {company} ({exchange}) — {ann_type}")

    # OPTIMIZATION 1: Check database by company+date+type BEFORE downloading PDF
    # This handles both exact duplicates and cross-exchange duplicates
    try:
        existing = db.find_announcement_by_company_date(company, date, ann_type)
    except Exception as e:
        logger.error(f"Error checking existing announcement: {e}")
        existing = None

    # For NSE, use pre-downloaded PDF bytes from stealth browser session
    # For BSE, download via httpx (no Akamai protection on BSE)
    pdf_bytes = announcement.pop("_pdf_bytes", None)
    if pdf_bytes is None:
        pdf_bytes = await download_pdf(pdf_url, exchange)

    if not pdf_bytes:
        # Only store announcements that have a real PDF. Skip PDF-less rows entirely (no
        # details-text fallback): a pdf_stored=0 row would reach discover / notifications with a
        # broken "View Original Filing" link, so we neither persist it nor send it downstream.
        logger.info(f"  Skipping {company}: no PDF (pdf_stored would be 0)")
        return

    # Generate stable ID from content hash (raw content validation)
    content_to_hash = pdf_bytes if pdf_bytes else details.encode()
    sha256_hash = hashlib.sha256(content_to_hash).hexdigest()
    # Hyphen-less (32-char hex) form of the deterministic UUID. The customer app's share-link
    # parser splits on '-' and keeps the last token, so a hyphenated UUID would be truncated to
    # its last group. Storing the id hyphen-less keeps ONE canonical format end-to-end (DB, news/
    # stockfeed API, share URL, notification) with no per-caller normalisation. uuid5(...).hex ==
    # str(uuid5(...)) with hyphens stripped, so the id-format backfill (REPLACE(id,'-','')) on
    # existing rows yields exactly the same value the crawler now generates.
    stable_id = uuid.uuid5(uuid.NAMESPACE_DNS, sha256_hash).hex

    # TWO-STEP VALIDATION: Check both metadata AND content hash
    if existing:
        existing_exchange = existing.get("exchange", "")
        existing_id = existing.get("id")
        existing_sha256 = existing.get("sha256", "")

        # If BOTH metadata AND content hash match, it's a true duplicate
        if sha256_hash == existing_sha256:
            # If different exchange (e.g., BSE when NSE exists), merge exchanges
            if exchange == "BSE" and "NSE" in existing_exchange:
                updated_data = {"exchange": "NSE,BSE"}
                db.update_announcement(existing_id, updated_data)
            return

    # Additional check: verify not already processed by content hash (different metadata, same content)
    existing_announcement = db.get_announcement(stable_id)
    if existing_announcement:
        return

    # Give up after MAX_SUMMARY_ATTEMPTS: stop re-processing an announcement whose summary has failed
    # that many times this run (avoids hammering the LLM forever on a permanently unparseable item).
    if _summary_attempts.get(stable_id, 0) >= MAX_SUMMARY_ATTEMPTS:
        return

    symbol_val = announcement.get("symbol", "")
    # ISIN resolves from the raw feed symbol (BSE keys on the scrip code), so do it first.
    isin_val = symbol_master.get_isin(exchange, symbol_val)
    # Then normalise to the unified symbol name via the quote DB so NSE and BSE rows store one
    # format (e.g. BSE scrip 500325 -> "RELIANCE"). Falls back to the raw feed value when the
    # quote DB is disabled/unreachable or has no match.
    resolved_name = symbol_master.get_symbol_name(exchange, symbol_val)
    if resolved_name:
        symbol_val = resolved_name
    metadata = {
        "company": company,
        "symbol": symbol_val,
        "isin": isin_val,
        "exchange": exchange,
        "announcement_type": ann_type,
        "date": date,
        "pdf_url": pdf_url,
        "sha256": sha256_hash,
    }

    ai_result = ai.process_announcement(pdf_bytes, metadata, fallback_text=details, custom_instruction=custom_instruction, labels=labels, label_priority=label_priority)

    # No summary even after the LLM's retries -> do NOT store the row. "Summary not available." is the
    # fallback ai_processor returns on LLM/config failure. Leaving the announcement unstored means a
    # later crawl cycle re-attempts it (dedup is keyed on the stored id), instead of persisting a
    # permanent summary-less card. Any PDF already uploaded to MinIO is reused on the retry. After
    # MAX_SUMMARY_ATTEMPTS tries the give-up gate above stops re-processing it.
    summary = (ai_result.get("summary") or "").strip()
    if not summary or summary == "Summary not available.":
        attempts = _summary_attempts.get(stable_id, 0) + 1
        _summary_attempts[stable_id] = attempts
        if attempts >= MAX_SUMMARY_ATTEMPTS:
            logger.warning(f"  Giving up - no summary after {attempts} attempts: {stable_id[:8]}... ({company})")
        else:
            logger.warning(f"  Skipping store (attempt {attempts}/{MAX_SUMMARY_ATTEMPTS}) - no summary: {stable_id[:8]}... ({company})")
        return None

    # Real summary obtained -> clear any prior failed-attempt count for this id.
    _summary_attempts.pop(stable_id, None)

    result_data = {
        "id": stable_id,
        "sha256": sha256_hash,
        "crawled_at": datetime.now(IST).isoformat(),
        "pdf_stored": bool(pdf_bytes),
        "cycle_id": cycle_id,
        **metadata,
        **ai_result,
    }

    # Store in MySQL database
    db.insert_announcement(result_data)
    logger.info(f"  Stored result in MySQL: {stable_id[:8]}...")

    return exchange


# ── Exchange monitoring ───────────────────────────────────────────────────────

def check_stale_exchanges():
    """
    Check if exchanges haven't returned new announcements for a long time.
    Sends Slack alerts if an exchange has been inactive for over 6 hours.
    """
    from datetime import timedelta

    ALERT_THRESHOLD_HOURS = 6

    try:
        # Get the last crawled time for each exchange
        last_nse = db.get_last_crawled_time("NSE")
        last_bse = db.get_last_crawled_time("BSE")

        now = datetime.now(IST)

        # Check NSE
        if last_nse:
            # Make timezone-aware if needed
            if last_nse.tzinfo is None:
                last_nse = last_nse.replace(tzinfo=timezone.utc)
            hours_since_nse = (now - last_nse).total_seconds() / 3600
            if hours_since_nse > ALERT_THRESHOLD_HOURS:
                logger.warning(f"⚠️ NSE: No new announcements for {hours_since_nse:.1f} hours")
                slack_notifier.notify_no_news_alert("NSE", int(hours_since_nse))

        # Check BSE
        if last_bse:
            # Make timezone-aware if needed
            if last_bse.tzinfo is None:
                last_bse = last_bse.replace(tzinfo=timezone.utc)
            hours_since_bse = (now - last_bse).total_seconds() / 3600
            if hours_since_bse > ALERT_THRESHOLD_HOURS:
                logger.warning(f"⚠️ BSE: No new announcements for {hours_since_bse:.1f} hours")
                slack_notifier.notify_no_news_alert("BSE", int(hours_since_bse))

    except Exception as e:
        logger.error(f"Error checking stale exchanges: {e}")


def check_pdf_storage_health():
    """
    Check if PDFs are being stored for each exchange.
    Sends a Slack alert if the last 5 consecutive announcements
    for any exchange have pdf_stored=0.
    """
    PDF_FAILURE_THRESHOLD = 5

    try:
        for exchange in ("NSE", "BSE"):
            if db.check_pdf_storage_failures(exchange, PDF_FAILURE_THRESHOLD):
                logger.warning(f"⚠️ {exchange}: Last {PDF_FAILURE_THRESHOLD} announcements have no PDF stored")
                slack_notifier.notify_pdf_storage_failure(exchange, PDF_FAILURE_THRESHOLD)
    except Exception as e:
        logger.error(f"Error checking PDF storage health: {e}")


# ── Crawl cycle ───────────────────────────────────────────────────────────────

async def run_crawl_cycle():
    cycle_started_at = datetime.now(IST).isoformat()
    logger.info(f"\n=== Crawl cycle started at {cycle_started_at} ===")

    prompt_config = db.get_prompt()
    custom_instruction = (
        prompt_config["instruction"]
        if prompt_config["instruction"] != db.DEFAULT_INSTRUCTION
        else None
    )
    labels = prompt_config.get("labels")
    label_priority = prompt_config.get("label_priority")
    if custom_instruction:
        logger.info(f"Using custom AI instruction ({len(custom_instruction)} chars)")
    if labels:
        logger.info(f"Using {len(labels)} classification labels from database")

    bse_items = await fetch_bse_announcements()
    await sb.random_delay(2, 5)
    nse_items = await fetch_nse_announcements()

    logger.info(f"Fetched — NSE: {len(nse_items)} | BSE: {len(bse_items)} announcements")

    # OPTIMIZATION: Deduplicate cross-exchange announcements BEFORE processing
    # Prefers NSE over BSE when same announcement exists in both
    all_items = deduplicate_announcements(nse_items, bse_items)
    logger.info(f"After cross-exchange dedup: {len(all_items)} announcements to process")

    results = await asyncio.gather(
        *[process_announcement(a, custom_instruction=custom_instruction, cycle_id=cycle_started_at, labels=labels, label_priority=label_priority) for a in all_items],
        return_exceptions=True,
    )

    # Count newly stored announcements (process_announcement returns exchange name on new save)
    exchanges = [r for r in results if isinstance(r, str)]
    new_nse = exchanges.count("NSE")
    new_bse = exchanges.count("BSE")
    new_total = len(exchanges)

    logger.info(f"=== Crawl cycle complete — {new_total} new ({new_nse} NSE, {new_bse} BSE) ===\n")

    slack_notifier.notify_cycle(
        new_total=new_total,
        new_nse=new_nse,
        new_bse=new_bse,
        fetched_nse=len(nse_items),
        fetched_bse=len(bse_items),
        cycle_started_at=cycle_started_at,
    )

    # Check for stale exchanges and send alerts if needed
    check_stale_exchanges()

    # Check if PDFs are being stored properly for each exchange
    check_pdf_storage_health()
