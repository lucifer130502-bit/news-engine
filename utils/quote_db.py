"""
Quote DB ISIN lookup.

Looks up ISIN by symbol from the SAMCO `quote_data` MySQL — table NSE_QUOTE
(keyed by `symbol`) or BSE_QUOTE (keyed by `scrip_code`).

Falls back silently (returns None) when the quote DB is disabled, unreachable,
or returns nothing. The caller (symbol_master.py) chains us with the public
NSE/BSE endpoints.
"""
import logging
import threading
from typing import Optional

from mysql.connector import pooling

from config.config_context import config

logger = logging.getLogger(__name__)

_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    """Lazy connection pool. Returns None if quote DB is not configured."""
    global _pool
    quote_cfg = getattr(config, "quote_mysql", None)
    if quote_cfg is None or not quote_cfg.enabled or not quote_cfg.host:
        return None

    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            _pool = pooling.MySQLConnectionPool(
                pool_name="quote_db_pool",
                pool_size=3,
                host=quote_cfg.host,
                port=quote_cfg.port,
                user=quote_cfg.user,
                password=quote_cfg.password,
                database=quote_cfg.database,
                autocommit=True,
                connection_timeout=10,
            )
            logger.info(f"Quote DB pool initialised at {quote_cfg.host}:{quote_cfg.port}/{quote_cfg.database}")
        except Exception as e:
            logger.warning(f"Quote DB pool init failed: {e}")
            _pool = None
        return _pool


def get_symbol_name(exchange: Optional[str], symbol: Optional[str]) -> Optional[str]:
    """
    Resolve the unified symbol name (e.g. "RELIANCE") from the quote DB so both NSE and
    BSE announcements can be stored in a single format.

      NSE_QUOTE: keyed by SYMBOL_NAME (the NSE feed already provides the name).
      BSE_QUOTE: keyed by SCRIP_CODE -> SYMBOL_NAME (e.g. 500325 -> RELIANCE).

    Returns None when the quote DB is disabled / unreachable / has no match — the caller
    keeps the original feed value (BSE scrip / NSE symbol) so crawling never blocks on this.
    """
    if not symbol:
        return None
    sym = symbol.strip()
    if not sym:
        return None
    pool = _get_pool()
    if pool is None:
        return None

    exchanges = [e.strip().upper() for e in (exchange or "").split(",") if e.strip()]
    conn = None
    cursor = None
    try:
        conn = pool.get_connection()
        cursor = conn.cursor()

        if "NSE" in exchanges:
            cursor.execute(
                "SELECT SYMBOL_NAME FROM NSE_QUOTE WHERE SYMBOL_NAME = %s "
                "AND SYMBOL_NAME IS NOT NULL AND SYMBOL_NAME <> '' LIMIT 1",
                (sym.upper(),),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0].strip()

        if "BSE" in exchanges:
            cursor.execute(
                "SELECT SYMBOL_NAME FROM BSE_QUOTE WHERE SCRIP_CODE = %s "
                "AND SYMBOL_NAME IS NOT NULL AND SYMBOL_NAME <> '' LIMIT 1",
                (sym,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0].strip()

        return None
    except Exception as e:
        logger.debug(f"Quote DB symbol-name lookup failed for ({exchange}, {symbol}): {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_isin(exchange: Optional[str], symbol: Optional[str]) -> Optional[str]:
    """
    Resolve ISIN from quote DB. Returns None if disabled / unreachable / no match —
    caller should fall back to public endpoints.
    """
    if not symbol:
        return None
    pool = _get_pool()
    if pool is None:
        return None

    exchanges = [e.strip().upper() for e in (exchange or "").split(",") if e.strip()]
    conn = None
    cursor = None
    try:
        conn = pool.get_connection()
        cursor = conn.cursor()

        if "NSE" in exchanges:
            cursor.execute(
                "SELECT isin FROM NSE_QUOTE WHERE symbol = %s AND isin IS NOT NULL AND isin <> '' LIMIT 1",
                (symbol.strip().upper(),),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0].strip()

        if "BSE" in exchanges:
            cursor.execute(
                "SELECT isin FROM BSE_QUOTE WHERE scrip_code = %s AND isin IS NOT NULL AND isin <> '' LIMIT 1",
                (symbol.strip(),),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0].strip()

        return None
    except Exception as e:
        logger.debug(f"Quote DB ISIN lookup failed for ({exchange}, {symbol}): {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_token_symbol(exchange: Optional[str], symbol: Optional[str]) -> Optional[str]:
    """
    Build the watchlist/holdings symbol form ``<token>_<exchange>`` (e.g. "2885_NSE")
    from the unified symbol name, so the SendAINews audience procedure can match
    STOCKNOTES_USER_WATCHLIST_SYMBOLS / HoldingsPositions / *_FAV_SYMBOLS.

      NSE -> NSE_QUOTE.TOKEN      keyed by SYMBOL_NAME -> "<token>_NSE"
      BSE -> BSE_QUOTE.SCRIP_CODE keyed by SYMBOL_NAME -> "<scrip_code>_BSE"

    NSE is preferred when the announcement is on both exchanges. Returns None when the
    quote DB is disabled / unreachable / has no match — the caller keeps the name form.
    """
    if not symbol:
        return None
    sym = symbol.strip()
    if not sym:
        return None
    pool = _get_pool()
    if pool is None:
        return None

    exchanges = [e.strip().upper() for e in (exchange or "").split(",") if e.strip()]
    conn = None
    cursor = None
    try:
        conn = pool.get_connection()
        cursor = conn.cursor()

        if "NSE" in exchanges:
            cursor.execute(
                "SELECT TOKEN FROM NSE_QUOTE WHERE SYMBOL_NAME = %s "
                "AND TOKEN IS NOT NULL AND TOKEN <> '' LIMIT 1",
                (sym.upper(),),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return f"{str(row[0]).strip()}_NSE"

        if "BSE" in exchanges:
            cursor.execute(
                "SELECT SCRIP_CODE FROM BSE_QUOTE WHERE SYMBOL_NAME = %s "
                "AND SCRIP_CODE IS NOT NULL AND SCRIP_CODE <> '' LIMIT 1",
                (sym.upper(),),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return f"{str(row[0]).strip()}_BSE"

        return None
    except Exception as e:
        logger.debug(f"Quote DB token-symbol lookup failed for ({exchange}, {symbol}): {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
