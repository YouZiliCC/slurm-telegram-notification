"""
db.py — SQLite store for all sent notifications.

All records are kept permanently. The telegram_msg_ids column stores
the Telegram message IDs (JSON array) so that overflow messages beyond
MAX_MESSAGES can be deleted from the chat while staying in the DB.
"""

import json
import os
import sqlite3
import threading
import time

MAX_MESSAGES = int(os.getenv("MAX_MESSAGES", "5"))
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "messages.db"))

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def init_db() -> None:
    """Create the messages table if it does not exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type       TEXT    NOT NULL,
            job_id           TEXT    NOT NULL,
            summary          TEXT    NOT NULL,
            telegram_msg_ids TEXT,
            created_at       REAL   NOT NULL
        )
    """)
    conn.commit()


def record_message(event_type: str, job_id: str, summary: str,
                   telegram_msg_ids: list[int] | None = None) -> None:
    """Insert a message record. No rows are ever deleted."""
    conn = _get_conn()
    ids_json = json.dumps(telegram_msg_ids) if telegram_msg_ids else None
    conn.execute(
        "INSERT INTO messages (event_type, job_id, summary, telegram_msg_ids, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (event_type, job_id, summary, ids_json, time.time()),
    )
    conn.commit()


def get_overflow_records() -> list[dict]:
    """Return records whose Telegram messages should be deleted.

    These are records that are NOT among the latest MAX_MESSAGES *and*
    still have a non-null telegram_msg_ids (i.e. not yet cleaned up).
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT id, telegram_msg_ids FROM messages
        WHERE telegram_msg_ids IS NOT NULL
          AND id NOT IN (
              SELECT id FROM messages ORDER BY id DESC LIMIT ?
          )
        ORDER BY id ASC
    """, (MAX_MESSAGES,)).fetchall()
    return [{"id": r["id"], "telegram_msg_ids": json.loads(r["telegram_msg_ids"])} for r in rows]


def clear_telegram_ids(record_id: int) -> None:
    """Mark a record's Telegram messages as deleted (set to NULL)."""
    conn = _get_conn()
    conn.execute("UPDATE messages SET telegram_msg_ids = NULL WHERE id = ?", (record_id,))
    conn.commit()


def get_recent_messages() -> list[dict]:
    """Return the most recent messages, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, event_type, job_id, summary, telegram_msg_ids, created_at "
        "FROM messages ORDER BY id DESC LIMIT ?",
        (MAX_MESSAGES,),
    ).fetchall()
    return [dict(r) for r in rows]
