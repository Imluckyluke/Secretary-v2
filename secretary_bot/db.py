"""SQLite access layer.

All blocking sqlite access is serialized through a single asyncio.Lock and
pushed to a worker thread via asyncio.to_thread, so a slow/contended DB call
can never block the aiohttp event loop (which was the root cause of requests
piling up and turning into "database is locked" errors under concurrent
webhook traffic).
"""

import asyncio
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional

from secretary_bot.config import DB_PATH, OWNER_ID

_DB_LOCK = asyncio.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    # busy_timeout makes sqlite retry internally (up to 30s) instead of
    # raising "database is locked" the instant it hits contention.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _init_db_sync():
    """Runs once at startup: creates tables and runs migrations.

    This used to run on every db() call (i.e. multiple times per incoming
    message), which under concurrent webhook traffic was the main source of
    DB lock contention. Now it only runs once.
    """
    conn = _connect()
    conn.execute("PRAGMA journal_mode = WAL")  # allows concurrent readers/writers

    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            chat_name TEXT,
            sender_id INTEGER,
            sender_name TEXT,
            sender_username TEXT,
            text TEXT,
            media_type TEXT,
            file_id TEXT,
            ts TEXT
        )
    """)
    msg_cols = _column_names(conn, "messages")
    if "business_connection_id" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN business_connection_id TEXT")
    if "owner_user_id" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN owner_user_id INTEGER")
    if "message_id" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN message_id INTEGER")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_chat ON messages(chat_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conn ON messages(business_connection_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_owner ON messages(owner_user_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_lookup ON messages(business_connection_id, chat_id, message_id)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            reply_text TEXT NOT NULL
        )
    """)
    kw_cols = _column_names(conn, "keywords")
    if "owner_user_id" not in kw_cols:
        conn.execute("ALTER TABLE keywords ADD COLUMN owner_user_id INTEGER")
        conn.execute("UPDATE keywords SET owner_user_id = ? WHERE owner_user_id IS NULL", (OWNER_ID,))

    conn.execute("""
        CREATE TABLE IF NOT EXISTS connections (
            business_connection_id TEXT PRIMARY KEY,
            user_id INTEGER,
            display_name TEXT,
            username TEXT,
            is_enabled INTEGER,
            connected_at TEXT
        )
    """)

    # backfill owner_user_id on messages for rows written before this column existed
    conn.execute("""
        UPDATE messages SET owner_user_id = (
            SELECT user_id FROM connections WHERE connections.business_connection_id = messages.business_connection_id
        )
        WHERE owner_user_id IS NULL AND business_connection_id IS NOT NULL
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS home_groups (
            user_id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            chat_title TEXT,
            set_at TEXT
        )
    """)

    # topics / backup_topics are keyed by owner_user_id (stable per real
    # account), not business_connection_id (which changes if the account
    # reconnects the bot).
    topic_cols = _column_names(conn, "topics")
    if topic_cols and "owner_user_id" not in topic_cols:
        conn.execute("ALTER TABLE topics RENAME TO topics_legacy")
        topic_cols = []
    if not topic_cols:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                home_chat_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                chat_name TEXT,
                UNIQUE(owner_user_id, chat_id)
            )
        """)

    backup_topic_cols = _column_names(conn, "backup_topics")
    if backup_topic_cols and "owner_user_id" not in backup_topic_cols:
        conn.execute("ALTER TABLE backup_topics RENAME TO backup_topics_legacy")
        backup_topic_cols = []
    if not backup_topic_cols:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                home_chat_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                chat_name TEXT,
                UNIQUE(owner_user_id, chat_id)
            )
        """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS unified_media_topic (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            home_chat_id INTEGER NOT NULL,
            topic_id INTEGER NOT NULL
        )
    """)

    # Users being watched for profile changes (name / username / bio).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER NOT NULL,
            watched_user_id INTEGER NOT NULL,
            business_connection_id TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            bio TEXT,
            created_at TEXT,
            UNIQUE(owner_user_id, watched_user_id)
        )
    """)

    conn.commit()
    conn.close()


async def init_db():
    async with _DB_LOCK:
        await asyncio.to_thread(_init_db_sync)


async def run_db(fn: Callable[[sqlite3.Connection], object]):
    """Run `fn(conn)` against a fresh connection in a worker thread, holding
    the shared lock so writes never race each other, then commit + close.
    Returns whatever `fn` returns.
    """

    def _work():
        conn = _connect()
        try:
            result = fn(conn)
            conn.commit()
            return result
        finally:
            conn.close()

    async with _DB_LOCK:
        return await asyncio.to_thread(_work)


# ---------------------------------------------------------------------------
# Connections / accounts
# ---------------------------------------------------------------------------

def is_self_account_sync(conn: sqlite3.Connection, user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    row = conn.execute("SELECT 1 FROM connections WHERE user_id = ?", (user_id,)).fetchone()
    return row is not None


async def is_self_account(user_id: int) -> bool:
    return await run_db(lambda conn: is_self_account_sync(conn, user_id))


async def connection_owner_user_id(business_connection_id: str) -> Optional[int]:
    def _q(conn):
        row = conn.execute(
            "SELECT user_id FROM connections WHERE business_connection_id = ?",
            (business_connection_id,),
        ).fetchone()
        return row[0] if row else None

    return await run_db(_q)


async def latest_business_connection_id(owner_user_id: int) -> Optional[str]:
    """Most recent business_connection_id for a given owner user."""

    def _q(conn):
        row = conn.execute(
            "SELECT business_connection_id FROM connections WHERE user_id = ? ORDER BY connected_at DESC LIMIT 1",
            (owner_user_id,),
        ).fetchone()
        return row[0] if row else None

    return await run_db(_q)


async def upsert_connection(business_connection_id: str, user_id: int, display_name: str,
                             username: Optional[str], is_enabled: bool) -> bool:
    """Insert or update a business connection. Returns True if it already existed."""

    def _work(conn):
        existed = conn.execute(
            "SELECT 1 FROM connections WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.execute(
            """
            INSERT INTO connections (business_connection_id, user_id, display_name, username, is_enabled, connected_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(business_connection_id) DO UPDATE SET
                user_id=excluded.user_id,
                display_name=excluded.display_name,
                username=excluded.username,
                is_enabled=excluded.is_enabled,
                connected_at=excluded.connected_at
            """,
            (business_connection_id, user_id, display_name, username, 1 if is_enabled else 0, now_iso()),
        )
        return existed is not None

    return await run_db(_work)


async def connection_display_name(owner_user_id: int) -> str:
    def _q(conn):
        return conn.execute(
            "SELECT display_name, username FROM connections WHERE user_id = ? ORDER BY connected_at DESC LIMIT 1",
            (owner_user_id,),
        ).fetchone()

    row = await run_db(_q)
    if not row:
        return str(owner_user_id)
    display_name, username = row
    return display_name or (f"@{username}" if username else str(owner_user_id))


# ---------------------------------------------------------------------------
# Home groups
# ---------------------------------------------------------------------------

async def get_home_chat_id(user_id: int) -> Optional[int]:
    def _q(conn):
        row = conn.execute("SELECT chat_id FROM home_groups WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if row else None

    return await run_db(_q)


async def get_group_owner_user_id(chat_id: int) -> Optional[int]:
    def _q(conn):
        row = conn.execute("SELECT user_id FROM home_groups WHERE chat_id = ?", (chat_id,)).fetchone()
        return row[0] if row else None

    return await run_db(_q)


async def register_home_group(user_id: int, chat_id: int, chat_title: str):
    def _w(conn):
        conn.execute(
            """
            INSERT INTO home_groups (user_id, chat_id, chat_title, set_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id, chat_title=excluded.chat_title, set_at=excluded.set_at
            """,
            (user_id, chat_id, chat_title, now_iso()),
        )

    await run_db(_w)


# ---------------------------------------------------------------------------
# Messages (log + edit/delete tracking)
# ---------------------------------------------------------------------------

async def insert_message(*, chat_id: int, chat_name: str, sender_id: Optional[int],
                          sender_name: str, sender_username: Optional[str], text: str,
                          media_type: Optional[str], file_id: Optional[str], ts: str,
                          business_connection_id: Optional[str], owner_user_id: Optional[int],
                          message_id: Optional[int]):
    def _insert(conn):
        conn.execute(
            """
            INSERT INTO messages
                (chat_id, chat_name, sender_id, sender_name, sender_username, text, media_type,
                 file_id, ts, business_connection_id, owner_user_id, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (chat_id, chat_name, sender_id, sender_name, sender_username, text, media_type,
             file_id, ts, business_connection_id, owner_user_id, message_id),
        )

    await run_db(_insert)


async def get_message_by_id(business_connection_id: str, chat_id: int, message_id: int):
    """Fetch the most recent stored row for a given (connection, chat, message_id)."""

    def _q(conn):
        return conn.execute(
            """
            SELECT id, text, sender_name, sender_username
            FROM messages
            WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (business_connection_id, chat_id, message_id),
        ).fetchone()

    return await run_db(_q)


async def update_message_text(row_id: int, new_text: str):
    def _w(conn):
        conn.execute("UPDATE messages SET text = ? WHERE id = ?", (new_text, row_id))

    await run_db(_w)


async def get_chat_name_for_owner(owner_user_id: int, chat_id: int) -> Optional[str]:
    def _q(conn):
        row = conn.execute(
            "SELECT chat_name FROM messages WHERE owner_user_id = ? AND chat_id = ? ORDER BY id DESC LIMIT 1",
            (owner_user_id, chat_id),
        ).fetchone()
        return row[0] if row else None

    return await run_db(_q)


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------

async def list_keywords(owner_user_id: int):
    def _q(conn):
        return conn.execute(
            "SELECT id, keyword, reply_text FROM keywords WHERE owner_user_id = ? ORDER BY id",
            (owner_user_id,),
        ).fetchall()

    return await run_db(_q)


async def get_keywords_for_matching(owner_user_id: int):
    def _q(conn):
        return conn.execute(
            "SELECT keyword, reply_text FROM keywords WHERE owner_user_id = ?", (owner_user_id,)
        ).fetchall()

    return await run_db(_q)


async def insert_keyword(keyword: str, reply_text: str, owner_user_id: int):
    def _insert(conn):
        conn.execute(
            "INSERT INTO keywords (keyword, reply_text, owner_user_id) VALUES (?, ?, ?)",
            (keyword, reply_text, owner_user_id),
        )

    await run_db(_insert)


async def get_keyword(kw_id: int, owner_user_id: int):
    def _q(conn):
        return conn.execute(
            "SELECT id, keyword, reply_text FROM keywords WHERE id = ? AND owner_user_id = ?",
            (kw_id, owner_user_id),
        ).fetchone()

    return await run_db(_q)


async def delete_keyword(kw_id: int, owner_user_id: int):
    def _work(conn):
        conn.execute("DELETE FROM keywords WHERE id = ? AND owner_user_id = ?", (kw_id, owner_user_id))
        return conn.execute(
            "SELECT id, keyword, reply_text FROM keywords WHERE owner_user_id = ? ORDER BY id",
            (owner_user_id,),
        ).fetchall()

    return await run_db(_work)


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

async def get_topic(owner_user_id: int, chat_id: int):
    def _q(conn):
        return conn.execute(
            "SELECT topic_id, home_chat_id FROM topics WHERE owner_user_id = ? AND chat_id = ?",
            (owner_user_id, chat_id),
        ).fetchone()

    return await run_db(_q)


async def upsert_topic(owner_user_id: int, chat_id: int, home_chat_id: int, topic_id: int, chat_name: str):
    def _upsert(conn):
        conn.execute(
            """
            INSERT INTO topics (owner_user_id, chat_id, home_chat_id, topic_id, chat_name) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, chat_id) DO UPDATE SET
                home_chat_id=excluded.home_chat_id, topic_id=excluded.topic_id, chat_name=excluded.chat_name
            """,
            (owner_user_id, chat_id, home_chat_id, topic_id, chat_name),
        )

    await run_db(_upsert)


async def get_unified_media_topic():
    def _q(conn):
        return conn.execute("SELECT topic_id, home_chat_id FROM unified_media_topic WHERE id = 1").fetchone()

    return await run_db(_q)


async def upsert_unified_media_topic(home_chat_id: int, topic_id: int):
    def _upsert(conn):
        conn.execute(
            """
            INSERT INTO unified_media_topic (id, home_chat_id, topic_id) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET home_chat_id=excluded.home_chat_id, topic_id=excluded.topic_id
            """,
            (home_chat_id, topic_id),
        )

    await run_db(_upsert)


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

async def get_backup_account_ids():
    def _q(conn):
        return conn.execute(
            """
            SELECT DISTINCT m.owner_user_id
            FROM messages m
            WHERE m.owner_user_id IS NOT NULL AND m.owner_user_id != ?
            """,
            (OWNER_ID,),
        ).fetchall()

    return await run_db(_q)


async def get_backup_chats(owner_user_id: int):
    def _q(conn):
        return conn.execute(
            "SELECT DISTINCT chat_id, chat_name FROM messages WHERE owner_user_id = ? ORDER BY chat_name COLLATE NOCASE",
            (owner_user_id,),
        ).fetchall()

    return await run_db(_q)


async def get_backup_messages(owner_user_id: int, chat_id: int):
    def _q(conn):
        return conn.execute(
            "SELECT chat_name, sender_name, sender_username, text, media_type, ts, file_id "
            "FROM messages WHERE owner_user_id = ? AND chat_id = ? ORDER BY ts",
            (owner_user_id, chat_id),
        ).fetchall()

    return await run_db(_q)


# ---------------------------------------------------------------------------
# Watched profiles
# ---------------------------------------------------------------------------

async def add_watched_profile(owner_user_id: int, watched_user_id: int, business_connection_id: str,
                               first_name: Optional[str], last_name: Optional[str],
                               username: Optional[str], bio: Optional[str]):
    def _upsert(conn):
        conn.execute(
            """
            INSERT INTO watched_profiles
                (owner_user_id, watched_user_id, business_connection_id, first_name, last_name, username, bio, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, watched_user_id) DO UPDATE SET
                business_connection_id=excluded.business_connection_id,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                username=excluded.username,
                bio=excluded.bio
            """,
            (owner_user_id, watched_user_id, business_connection_id, first_name, last_name,
             username, bio, now_iso()),
        )

    await run_db(_upsert)


async def remove_watched_profile(owner_user_id: int, watched_user_id: int) -> bool:
    def _work(conn):
        cur = conn.execute(
            "DELETE FROM watched_profiles WHERE owner_user_id = ? AND watched_user_id = ?",
            (owner_user_id, watched_user_id),
        )
        return cur.rowcount > 0

    return await run_db(_work)


async def list_watched_profiles(owner_user_id: Optional[int] = None):
    def _q(conn):
        if owner_user_id is None:
            return conn.execute(
                "SELECT id, owner_user_id, watched_user_id, business_connection_id, "
                "first_name, last_name, username, bio FROM watched_profiles"
            ).fetchall()
        return conn.execute(
            "SELECT id, owner_user_id, watched_user_id, business_connection_id, "
            "first_name, last_name, username, bio FROM watched_profiles WHERE owner_user_id = ?",
            (owner_user_id,),
        ).fetchall()

    return await run_db(_q)


async def update_watched_profile_snapshot(row_id: int, first_name: Optional[str], last_name: Optional[str],
                                           username: Optional[str], bio: Optional[str]):
    def _w(conn):
        conn.execute(
            "UPDATE watched_profiles SET first_name = ?, last_name = ?, username = ?, bio = ? WHERE id = ?",
            (first_name, last_name, username, bio, row_id),
        )

    await run_db(_w)
