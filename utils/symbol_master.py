"""
NSE + BSE symbol master → ISIN resolver.

NSE: bulk CSV (EQUITY_L.csv) loaded once and refreshed daily.
BSE: per-scripcode lookup via ComHeader, cached in-memory (BSE codes are immutable).
"""
import csv
import io
import logging
import threading
import time
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

NSE_EQUITY_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
BSE_COMHEADER_URL = "https://api.bseindia.com/BseIndiaAPI/api/ComHeader/w"

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}
BSE_HEADERS = {
    "User-Agent": NSE_HEADERS["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bseindia.com/",
}

_NSE_REFRESH_SECONDS = 24 * 60 * 60

_lock = threading.Lock()
_nse_map: Dict[str, str] = {}
_nse_loaded_at: float = 0.0
_bse_cache: Dict[str, Optional[str]] = {}


def _load_nse() -> None:
    """Pull NSE EQUITY_L.csv and rebuild in-memory map."""
    global _nse_map, _nse_loaded_at
    try:
        resp = requests.get(NSE_EQUITY_URL, headers=NSE_HEADERS, timeout=30)
        resp.raise_for_status()
        out: Dict[str, str] = {}
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            norm = {k.strip(): (v or "").strip() for k, v in row.items()}
            sym = norm.get("SYMBOL", "").upper()
            isin = norm.get("ISIN NUMBER", "")
            if sym and isin:
                out[sym] = isin
        if out:
            _nse_map = out
            _nse_loaded_at = time.time()
            logger.info(f"NSE symbol master loaded: {len(out)} symbols")
    except Exception as e:
        logger.warning(f"NSE symbol master fetch failed: {e}")


def _ensure_nse_loaded() -> None:
    if _nse_map and (time.time() - _nse_loaded_at) < _NSE_REFRESH_SECONDS:
        return
    with _lock:
        if _nse_map and (time.time() - _nse_loaded_at) < _NSE_REFRESH_SECONDS:
            return
        _load_nse()


def _bse_lookup(scripcode: str) -> Optional[str]:
    """Hit BSE ComHeader per scripcode and extract ISIN. Cache forever."""
    if scripcode in _bse_cache:
        return _bse_cache[scripcode]
    try:
        resp = requests.get(
            BSE_COMHEADER_URL,
            params={"quotetype": "EQ", "scripcode": scripcode, "seriesid": ""},
            headers=BSE_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        isin = (data.get("ISIN") or "").strip()
        result = isin or None
        _bse_cache[scripcode] = result
        return result
    except Exception as e:
        logger.debug(f"BSE ISIN lookup failed for scripcode={scripcode}: {e}")
        _bse_cache[scripcode] = None
        return None


def get_isin(exchange: Optional[str], symbol: Optional[str]) -> Optional[str]:
    """
    Resolve ISIN for a given (exchange, symbol).
    exchange may be 'NSE', 'BSE', or 'NSE,BSE'.

    Strategy (first hit wins):
      1. SAMCO quote DB (NSE_QUOTE / BSE_QUOTE) — fast internal lookup
      2. NSE bulk CSV — for NSE rows the quote DB might miss
      3. BSE ComHeader API — for BSE rows the quote DB might miss

    Returning None ultimately is fine: the caller writes NULL isin and the
    consumer (discover) tolerates that with no-data defaults.
    """
    if not symbol:
        return None
    sym = symbol.strip()
    if not sym:
        return None

    # 1. Primary: internal quote DB (no network call to public sites)
    try:
        import utils.quote_db as quote_db
        isin = quote_db.get_isin(exchange, sym)
        if isin:
            return isin
    except Exception as e:
        logger.debug(f"quote_db lookup raised, falling back to public: {e}")

    # 2 & 3. Fallback: existing public endpoints
    exchanges = [e.strip().upper() for e in (exchange or "").split(",") if e.strip()]

    if "NSE" in exchanges:
        _ensure_nse_loaded()
        isin = _nse_map.get(sym.upper())
        if isin:
            return isin

    if "BSE" in exchanges:
        return _bse_lookup(sym)

    return None


def get_symbol_name(exchange: Optional[str], symbol: Optional[str]) -> Optional[str]:
    """
    Resolve the unified symbol name (e.g. BSE scrip 500325 -> "RELIANCE") via the quote DB
    so announcements store one consistent format across exchanges. Returns None when the
    quote DB is disabled / unreachable / has no match — the caller keeps the raw feed value.
    """
    if not symbol:
        return None
    try:
        import utils.quote_db as quote_db
        return quote_db.get_symbol_name(exchange, symbol)
    except Exception as e:
        logger.debug(f"quote_db symbol-name lookup raised: {e}")
        return None


def get_token_symbol(exchange: Optional[str], symbol: Optional[str]) -> Optional[str]:
    """
    Resolve the ``<token>_<exchange>`` watchlist symbol form (e.g. "2885_NSE") from the
    unified symbol name via the quote DB, so the SendAINews audience procedure can match
    a user's watchlist / holdings / favourites. Returns None when the quote DB is
    disabled / unreachable / has no match — the caller keeps the name form.
    """
    if not symbol:
        return None
    try:
        import utils.quote_db as quote_db
        return quote_db.get_token_symbol(exchange, symbol)
    except Exception as e:
        logger.debug(f"quote_db token-symbol lookup raised: {e}")
        return None
