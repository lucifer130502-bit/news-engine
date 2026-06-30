from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import httpx
from config.config_context import config
import logging

logger = logging.getLogger(__name__)

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

TAGLINE = "NSE & BSE · AI-powered corporate filings"


def _build_payload(
    new_total: int,
    new_nse: int,
    new_bse: int,
    fetched_nse: int,
    fetched_bse: int,
    cycle_started_at: str,
) -> dict:
    now = datetime.now(IST).strftime("%d %b %Y, %H:%M IST")

    if new_total == 0:
        headline = "No new announcements this cycle."
        color = "#64748b"  # slate
    else:
        headline = f"{new_total} new announcement{'s' if new_total != 1 else ''} processed."
        color = "#10b981"  # emerald

    breakdown = f"NSE: *{new_nse}* new / {fetched_nse} fetched   |   BSE: *{new_bse}* new / {fetched_bse} fetched"

    return {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": "SAMCO Market Pulse",
                            "emoji": True,
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": TAGLINE,
                            }
                        ],
                    },
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*{headline}*\n{breakdown}",
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"Cycle completed at {now}",
                            }
                        ],
                    },
                ],
            }
        ]
    }


def notify_cycle(
    new_total: int,
    new_nse: int,
    new_bse: int,
    fetched_nse: int,
    fetched_bse: int,
    cycle_started_at: str = "",
) -> None:
    webhook_url = config.api_keys.slack_webhook_url
    if not webhook_url:
        logger.warning("Slack: slack_webhook_url not configured, skipping notification.")
        return

    payload = _build_payload(
        new_total=new_total,
        new_nse=new_nse,
        new_bse=new_bse,
        fetched_nse=fetched_nse,
        fetched_bse=fetched_bse,
        cycle_started_at=cycle_started_at,
    )

    try:
        resp = httpx.post(
            webhook_url,
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"Slack: notification sent ({new_total} new announcements).")
        else:
            logger.info(f"Slack: webhook returned {resp.status_code} — {resp.text}")
    except Exception as e:
        logger.error(f"Slack: notification failed — {e}")


def notify_no_news_alert(exchange: str, duration_hours: int) -> None:
    """
    Send a Slack alert when an exchange hasn't returned any new announcements
    for a prolonged period (indicating potential crawler or exchange issue).
    """
    webhook_url = config.api_keys.slack_webhook_url
    if not webhook_url:
        logger.warning("Slack: slack_webhook_url not configured, skipping alert.")
        return

    now = datetime.now(IST).strftime("%d %b %Y, %H:%M IST")

    payload = {
        "attachments": [
            {
                "color": "#ef4444",  # red - warning
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": "⚠️ SAMCO Market Pulse - Alert",
                            "emoji": True,
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": TAGLINE,
                            }
                        ],
                    },
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*No new {exchange} announcements for {duration_hours} hours*\n\nThe {exchange} crawler may be experiencing issues, or the exchange might not be publishing new announcements.",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Action Required:*\n• Check {exchange} website availability\n• Review crawler logs for errors\n• Verify exchange is publishing announcements",
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"Alert triggered at {now}",
                            }
                        ],
                    },
                ],
            }
        ]
    }

    try:
        resp = httpx.post(
            webhook_url,
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"Slack: no-news alert sent for {exchange} ({duration_hours}h).")
        else:
            logger.info(f"Slack: webhook returned {resp.status_code} — {resp.text}")
    except Exception as e:
        logger.error(f"Slack: alert notification failed — {e}")


def notify_pdf_storage_failure(exchange: str, consecutive_failures: int) -> None:
    """
    Send a Slack alert when PDF storage has failed for multiple
    consecutive announcements on an exchange.
    """
    webhook_url = config.api_keys.slack_webhook_url
    if not webhook_url:
        logger.warning("Slack: slack_webhook_url not configured, skipping alert.")
        return

    now = datetime.now(IST).strftime("%d %b %Y, %H:%M IST")

    payload = {
        "attachments": [
            {
                "color": "#f59e0b",  # amber - warning
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": "⚠️ SAMCO Market Pulse - PDF Storage Alert",
                            "emoji": True,
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": TAGLINE,
                            }
                        ],
                    },
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*{exchange} PDF crawling is not working as expected*\n\nThe last *{consecutive_failures}* {exchange} announcements have no PDF stored (`pdf_stored = 0`).",
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"Alert triggered at {now}",
                            }
                        ],
                    },
                ],
            }
        ]
    }

    try:
        resp = httpx.post(
            webhook_url,
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"Slack: PDF storage failure alert sent for {exchange}.")
        else:
            logger.info(f"Slack: webhook returned {resp.status_code} — {resp.text}")
    except Exception as e:
        logger.error(f"Slack: PDF storage alert failed — {e}")
