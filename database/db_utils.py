"""
Database utilities for connection retry and error handling.
"""
import functools
import logging
import time
from mysql.connector import errors as mysql_errors

logger = logging.getLogger(__name__)


def retry_on_disconnect(max_retries=2, delay=0.5):
    """
    Decorator: retry on MySQL disconnect/connection errors.

    Handles common MySQL errors:
    - 2006: MySQL server has gone away
    - 2013: Lost connection to MySQL server during query
    - Connection not available (from connection pool)

    Args:
        max_retries: Maximum number of retry attempts (default: 2)
        delay: Delay in seconds between retries (default: 0.5)

    Usage:
        @retry_on_disconnect(max_retries=3, delay=1.0)
        def my_db_function():
            conn = get_connection()
            # ... database operations
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except mysql_errors.OperationalError as e:
                    last_exception = e
                    error_code = getattr(e, 'errno', None)
                    error_msg = str(e)

                    # Check if it's a disconnect/connection error
                    is_disconnect_error = (
                        error_code in (2006, 2013) or  # MySQL server gone away / Lost connection
                        'Connection not available' in error_msg or
                        'Lost connection' in error_msg
                    )

                    if is_disconnect_error and attempt < max_retries:
                        logger.info(
                            f"[db_retry] MySQL connection error (code={error_code}) in {fn.__name__}. "
                            f"Retrying {attempt + 1}/{max_retries}..."
                        )
                        time.sleep(delay)
                        continue
                    else:
                        # Either not a disconnect error, or max retries exceeded
                        raise
                except Exception:
                    # Non-MySQL errors should not be retried
                    raise

            # If we get here, all retries failed
            if last_exception:
                raise last_exception

        return wrapper
    return decorator
