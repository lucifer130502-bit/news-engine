"""
Centralized logging configuration for the AI News Summarizer Engine.

Usage:
    from logs.logging_config import setup_logging
    setup_logging()

All modules can then simply use:
    import logging
    logger = logging.getLogger(__name__)
"""
import os
import uuid
import logging
from logging.handlers import RotatingFileHandler
from config.config_context import config

# Extract log directory and file from YAML config
LOG_DIR = config.log.dir
LOG_FILE = config.log.file

# Ensure directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Compose full path
LOG_PATH = os.path.join(LOG_DIR, LOG_FILE)

# ── Shared format constants ──
LOG_FORMAT = "%(asctime)s %(levelname)s %(message_id)s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ── Startup correlation ID ──
# A fixed UUID used for all engine logs (since engine is a background worker).
# This lets logs be grouped/traced in Grafana.
STARTUP_CORRELATION_ID = uuid.uuid4().hex


class SingleLineFormatter(logging.Formatter):
    """
    Custom formatter that ensures every log entry is a single line.
    Replaces newlines and carriage returns in messages so that
    large payloads, tracebacks, and multi-line strings don't break
    log parsing in Grafana/Loki or DB storage.
    """

    def format(self, record):
        # Ensure message_id is always present
        if not hasattr(record, 'message_id'):
            record.message_id = STARTUP_CORRELATION_ID
        original = super().format(record)
        # Replace all newline variants with a single space
        return original.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ')


class MessageIdFilter(logging.Filter):
    """
    Filter that adds message_id to every log record.
    For engine (background worker), all logs use the startup correlation ID.
    """

    def filter(self, record):
        if not hasattr(record, 'message_id'):
            record.message_id = STARTUP_CORRELATION_ID
        return True


def setup_logging():
    """
    Configure application-wide logging with console + rotating file handlers.

    Reads log directory and filename from config (config/<profile>.yaml):
        log:
          dir: "./logs"
          file: "ai-news-summarizer-engine.log"

    Features:
    - RotatingFileHandler: 5MB max size, 5 backup files
    - Console handler for development visibility
    - Single-line formatting for Grafana/Loki compatibility
    - Correlation ID support for log tracing
    - Matches AFD project logging format
    """
    formatter = SingleLineFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Clear any existing handlers to avoid duplicates
    if root_logger.handlers:
        root_logger.handlers.clear()

    # Create the filter instance
    message_id_filter = MessageIdFilter()

    # File Handler with rotation
    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(message_id_filter)
    root_logger.addHandler(file_handler)

    # Console Handler for development visibility
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(message_id_filter)
    root_logger.addHandler(console_handler)

    # ── Override third-party loggers so ALL logs use the same format ──
    for logger_name in (
        "LiteLLM", "LiteLLM Proxy", "LiteLLM Router",
        "litellm", "openai", "httpx",
    ):
        lib_logger = logging.getLogger(logger_name)
        lib_logger.handlers.clear()
        lib_logger.propagate = True  # Let root logger handle formatting

    logging.info(f"Logging initialized — level=INFO, file={LOG_PATH}, message_id={STARTUP_CORRELATION_ID}")
