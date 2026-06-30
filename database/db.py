"""
Database connection and operations for announcements.
"""
import mysql.connector
from mysql.connector import pooling
from datetime import datetime, date, timezone, timedelta
import json
from typing import Optional
from config.config_context import config
from database.db_utils import retry_on_disconnect
import logging

logger = logging.getLogger(__name__)

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# Database configuration
DB_CONFIG = {
    "host": config.mysql.host,
    "port": config.mysql.port,
    "user": config.mysql.user,
    "password": config.mysql.password,
    "database": config.mysql.database,
}

# Connection pool
connection_pool = None
schema_checked = False


CREATE_ANNOUNCEMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS announcements (
    id VARCHAR(64) PRIMARY KEY,
    company VARCHAR(512),
    symbol VARCHAR(64),
    isin VARCHAR(20),
    exchange VARCHAR(20),
    announcement_type VARCHAR(512),
    classification_label VARCHAR(512) DEFAULT 'Other Announcement',
    date DATE,
    pdf_url TEXT,
    pdf_stored TINYINT(1) DEFAULT 0,
    summary TEXT,
    sentiment VARCHAR(20),
    sentiment_score FLOAT DEFAULT 0.0,
    relevance VARCHAR(20),
    key_points JSON,
    crawled_at DATETIME,
    sha256 VARCHAR(64),
    cycle_id VARCHAR(64),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_company_date_type (company, date, announcement_type),
    INDEX idx_exchange (exchange),
    INDEX idx_crawled_at (crawled_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

CREATE_AI_PROMPTS_TABLE = """
CREATE TABLE IF NOT EXISTS ai_prompts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    instruction TEXT,
    labels JSON,
    label_priority JSON,
    is_active TINYINT(1) DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def _ensure_tables(pool) -> None:
    conn = pool.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(CREATE_ANNOUNCEMENTS_TABLE)
        cursor.execute(CREATE_AI_PROMPTS_TABLE)
        conn.commit()
        logger.info("Ensured announcements and ai_prompts tables exist")
    finally:
        cursor.close()
        conn.close()


def get_pool():
    """Get or create database connection pool."""
    global connection_pool, schema_checked
    if connection_pool is None:
        connection_pool = pooling.MySQLConnectionPool(
            pool_name="announcements_pool",
            pool_size=5,
            **DB_CONFIG
        )
    if not schema_checked:
        _ensure_tables(connection_pool)
        schema_checked = True
    return connection_pool


def get_connection():
    """Get a connection from the pool.

    Ping + reconnect before handing it out, so a pooled connection the server closed
    during a long idle gap (e.g. the slow LLM calls between DB ops in the summary
    backfill) is revived instead of failing the next query with 2006/2013
    ("server gone away" / "Lost connection during query")."""
    pool = get_pool()
    conn = pool.get_connection()
    try:
        conn.ping(reconnect=True, attempts=3, delay=1)
    except Exception:
        # Ping itself failed after reconnect attempts; return the conn anyway and let
        # the caller's query surface/retry the error rather than masking it here.
        pass
    return conn


def labels_to_db(labels) -> str:
    """Serialise a list of classification labels into the comma-separated string stored in
    the `classification_label` column. Falls back to the default when empty."""
    if isinstance(labels, str):
        labels = [labels]
    cleaned = [str(label).strip() for label in (labels or []) if str(label).strip()]
    return ", ".join(cleaned) if cleaned else "Other Announcement"


def labels_from_db(value) -> list:
    """Parse the comma-separated `classification_label` string back into a list of labels.
    A single label stays a one-element list; defaults to ['Other Announcement']."""
    if not value:
        return ["Other Announcement"]
    parts = [part.strip() for part in str(value).split(",") if part.strip()]
    return parts or ["Other Announcement"]


@retry_on_disconnect(max_retries=2, delay=0.5)
def insert_announcement(data: dict) -> bool:
    """Insert a new announcement into the database."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Convert key_points list to JSON string
        key_points_json = json.dumps(data.get("key_points", []))
        # Classification labels persist as a comma-separated string in `classification_label`.
        classification_label = labels_to_db(data.get("classification_label"))
        
        # Parse date string to date object
        date_obj = datetime.fromisoformat(data.get("date")).date() if isinstance(data.get("date"), str) else data.get("date")
        
        # Parse crawled_at to datetime
        crawled_at = data.get("crawled_at")
        if isinstance(crawled_at, str):
            if crawled_at.endswith('Z'):
                crawled_at = datetime.fromisoformat(crawled_at.replace('Z', '+00:00'))
            else:
                crawled_at = datetime.fromisoformat(crawled_at)
        
        sql = """
        INSERT INTO announcements
        (id, company, symbol, isin, exchange, announcement_type, classification_label,
         date, pdf_url, pdf_stored, summary, sentiment, relevance, key_points, crawled_at, sha256, cycle_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        values = (
            data.get("id"),
            data.get("company"),
            data.get("symbol"),
            data.get("isin"),
            data.get("exchange"),
            data.get("announcement_type"),
            classification_label,
            date_obj,
            data.get("pdf_url"),
            bool(data.get("pdf_stored", False)),
            data.get("summary"),
            data.get("sentiment"),
            data.get("relevance"),
            key_points_json,
            crawled_at,
            data.get("sha256"),
            data.get("cycle_id"),
        )
        
        cursor.execute(sql, values)
        conn.commit()
        return True

    except mysql.connector.IntegrityError:
        # Duplicate entry, ignore
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


def update_announcement(announcement_id: str, data: dict) -> bool:
    """Update an existing announcement."""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Build update query dynamically based on provided fields
        update_fields = []
        values = []
        
        if "exchange" in data:
            update_fields.append("exchange = %s")
            values.append(data["exchange"])
        
        if "summary" in data:
            update_fields.append("summary = %s")
            values.append(data["summary"])
        
        if "sentiment" in data:
            update_fields.append("sentiment = %s")
            values.append(data["sentiment"])
        
        if "relevance" in data:
            update_fields.append("relevance = %s")
            values.append(data["relevance"])
        
        if "key_points" in data:
            update_fields.append("key_points = %s")
            values.append(json.dumps(data["key_points"]))

        if "classification_label" in data:
            update_fields.append("classification_label = %s")
            values.append(labels_to_db(data["classification_label"]))
        
        if not update_fields:
            return False
        
        values.append(announcement_id)
        sql = f"UPDATE announcements SET {', '.join(update_fields)} WHERE id = %s"
        
        cursor.execute(sql, values)
        conn.commit()
        return cursor.rowcount > 0
        
    except Exception as e:
        logger.error(f"Error updating announcement: {e}")
        # Defensive: if the connection is already dead, rollback() itself raises
        # (2013) — don't let that escape and abort the whole backfill.
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            cursor.close()
        except:
            pass
        try:
            conn.close()
        except:
            pass


@retry_on_disconnect(max_retries=2, delay=0.5)
def get_announcement(announcement_id: str) -> Optional[dict]:
    """Get a single announcement by ID."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        sql = "SELECT * FROM announcements WHERE id = %s"
        cursor.execute(sql, (announcement_id,))
        result = cursor.fetchone()
        
        if result:
            # Parse JSON fields
            if result.get("key_points"):
                result["key_points"] = json.loads(result["key_points"])
            result["classification_label"] = labels_from_db(result.get("classification_label"))
            # Convert date to string
            if result.get("date"):
                result["date"] = result["date"].isoformat()
            # Convert datetime to string
            if result.get("crawled_at"):
                result["crawled_at"] = result["crawled_at"].isoformat()
            if result.get("created_at"):
                result["created_at"] = result["created_at"].isoformat()
            if result.get("updated_at"):
                result["updated_at"] = result["updated_at"].isoformat()

        return result
    finally:
        cursor.close()
        conn.close()


@retry_on_disconnect(max_retries=2, delay=0.5)
def find_announcement_by_company_date(company: str, announcement_date: str, announcement_type: str = None) -> Optional[dict]:
    """Find an announcement by company name, date, and optionally type (for cross-exchange dedup)."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Parse date
        date_obj = datetime.fromisoformat(announcement_date).date() if isinstance(announcement_date, str) else announcement_date

        # Include announcement_type in query if provided
        if announcement_type:
            # Force use of idx_company_date_type for optimal performance
            sql = "SELECT * FROM announcements USE INDEX (idx_company_date_type) WHERE company = %s AND date = %s AND announcement_type = %s LIMIT 1"
            cursor.execute(sql, (company, date_obj, announcement_type))
        else:
            sql = "SELECT * FROM announcements WHERE company = %s AND date = %s LIMIT 1"
            cursor.execute(sql, (company, date_obj))
        result = cursor.fetchone()
        
        if result:
            # Parse JSON fields
            if result.get("key_points"):
                result["key_points"] = json.loads(result["key_points"])
            result["classification_label"] = labels_from_db(result.get("classification_label"))
            # Convert date to string
            if result.get("date"):
                result["date"] = result["date"].isoformat()
            # Convert datetime to string
            if result.get("crawled_at"):
                result["crawled_at"] = result["crawled_at"].isoformat()
            if result.get("created_at"):
                result["created_at"] = result["created_at"].isoformat()
            if result.get("updated_at"):
                result["updated_at"] = result["updated_at"].isoformat()

        return result
    finally:
        cursor.close()
        conn.close()


DEFAULT_INSTRUCTION = (
    "You are a financial analyst assistant. "
    "Analyze the following corporate announcement and return a JSON object."
)


@retry_on_disconnect(max_retries=2, delay=0.5)
def get_prompt() -> dict:
    """Get the currently active AI prompt from MySQL.
    Returns dict with 'instruction', 'labels', 'label_priority', and 'updated_at'.
    'labels' is a list of {name, explanation} dicts; 'label_priority' is a list of
    label names ordered by importance. Both are None when not configured in the DB,
    causing ai_processor to fall back to its hardcoded defaults.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        sql = (
            "SELECT instruction, labels, label_priority, updated_at "
            "FROM ai_prompts WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1"
        )
        cursor.execute(sql)
        result = cursor.fetchone()

        if result:
            updated_at = result["updated_at"]
            if updated_at and hasattr(updated_at, "isoformat"):
                updated_at = updated_at.isoformat()
            labels = result.get("labels")
            if isinstance(labels, str):
                labels = json.loads(labels)
            label_priority = result.get("label_priority")
            if isinstance(label_priority, str):
                label_priority = json.loads(label_priority)
            return {
                "instruction": result["instruction"],
                "labels": labels,
                "label_priority": label_priority,
                "updated_at": updated_at,
            }
        return {"instruction": DEFAULT_INSTRUCTION, "labels": None, "label_priority": None, "updated_at": None}
    finally:
        cursor.close()
        conn.close()


def save_prompt(instruction: str) -> dict:
    """Save a new AI prompt to MySQL, deactivating previous ones.
    Preserves history by keeping old rows with is_active=0.
    The labels and label_priority are carried forward from the currently active row
    so that updating the instruction doesn't lose the label configuration.
    Returns dict with 'instruction', 'labels', 'label_priority', and 'updated_at'.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Carry forward label config from the current active row
        cursor.execute(
            "SELECT labels, label_priority FROM ai_prompts WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1"
        )
        current = cursor.fetchone()
        carried_labels = current[0] if current else None        # raw JSON string from MySQL
        carried_priority = current[1] if current else None      # raw JSON string from MySQL

        # Deactivate all existing active prompts
        cursor.execute("UPDATE ai_prompts SET is_active = 0 WHERE is_active = 1")

        # Insert the new active prompt, preserving label config
        sql = "INSERT INTO ai_prompts (instruction, labels, label_priority, is_active) VALUES (%s, %s, %s, 1)"
        cursor.execute(sql, (instruction, carried_labels, carried_priority))
        conn.commit()

        # Fetch the inserted row's updated_at
        cursor.execute(
            "SELECT updated_at FROM ai_prompts WHERE id = %s",
            (cursor.lastrowid,),
        )
        row = cursor.fetchone()
        updated_at = row[0].isoformat() if row and row[0] else datetime.now(IST).isoformat()

        labels = json.loads(carried_labels) if isinstance(carried_labels, str) else carried_labels
        label_priority = json.loads(carried_priority) if isinstance(carried_priority, str) else carried_priority

        return {
            "instruction": instruction,
            "labels": labels,
            "label_priority": label_priority,
            "updated_at": updated_at,
        }

    except Exception as e:
        logger.error(f"Error saving prompt: {e}")
        conn.rollback()
        raise RuntimeError(f"Failed to save prompt: {e}") from e
    finally:
        try:
            cursor.close()
        except:
            pass
        try:
            conn.close()
        except:
            pass


def get_prompt_history(page: int = 1, page_size: int = 20) -> dict:
    """Get the history of all AI prompts, newest first."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Get total count
        cursor.execute("SELECT COUNT(*) as total FROM ai_prompts")
        total = cursor.fetchone()["total"]

        # Get paginated results
        offset = (page - 1) * page_size
        sql = "SELECT id, instruction, is_active, created_at, updated_at FROM ai_prompts ORDER BY created_at DESC LIMIT %s OFFSET %s"
        cursor.execute(sql, (page_size, offset))
        results = cursor.fetchall()

        for r in results:
            if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
                r["created_at"] = r["created_at"].isoformat()
            if r.get("updated_at") and hasattr(r["updated_at"], "isoformat"):
                r["updated_at"] = r["updated_at"].isoformat()
            r["is_active"] = bool(r.get("is_active"))

        return {"items": results, "total": total, "page": page, "page_size": page_size}

    except Exception as e:
        logger.error(f"Error getting prompt history: {e}")
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    finally:
        try:
            cursor.close()
        except:
            pass
        try:
            conn.close()
        except:
            pass


@retry_on_disconnect(max_retries=2, delay=0.5)
def get_last_crawl_time() -> Optional[datetime]:
    """Get the timestamp of the most recently crawled announcement."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        sql = "SELECT MAX(crawled_at) as last_crawl FROM announcements"
        cursor.execute(sql)
        result = cursor.fetchone()

        if result and result.get("last_crawl"):
            return result["last_crawl"]
        return None
    finally:
        cursor.close()
        conn.close()


@retry_on_disconnect(max_retries=2, delay=0.5)
def get_last_crawled_time(exchange: str) -> Optional[datetime]:
    """
    Get the timestamp of the most recently crawled announcement for a specific exchange.
    Used for monitoring exchange health and detecting stale data.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        sql = "SELECT MAX(crawled_at) as last_crawl FROM announcements WHERE exchange LIKE %s"
        cursor.execute(sql, (f"%{exchange}%",))
        result = cursor.fetchone()

        if result and result.get("last_crawl"):
            return result["last_crawl"]
        return None
    finally:
        cursor.close()
        conn.close()


def check_pdf_storage_failures(exchange: str, threshold: int = 5) -> bool:
    """
    Check if the last `threshold` announcements for an exchange all have pdf_stored=0.
    Returns True if there is a failure streak >= threshold.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        sql = """
            SELECT pdf_stored FROM announcements
            WHERE exchange LIKE %s
            ORDER BY crawled_at DESC
            LIMIT %s
        """
        cursor.execute(sql, (f"%{exchange}%", threshold))
        rows = cursor.fetchall()

        if len(rows) < threshold:
            return False

        return all(not row["pdf_stored"] for row in rows)
    finally:
        cursor.close()
        conn.close()


@retry_on_disconnect(max_retries=2, delay=0.5)
def truncate_announcements() -> None:
    """Truncate the announcements table (called daily at 00:01 IST)."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("TRUNCATE TABLE announcements")
        conn.commit()
        logger.info("Truncated announcements table")
    finally:
        cursor.close()
        conn.close()


def test_connection():
    """Test database connection."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
        conn.close()
        logger.info("Database connection successful")
        return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False


if __name__ == "__main__":
    test_connection()
