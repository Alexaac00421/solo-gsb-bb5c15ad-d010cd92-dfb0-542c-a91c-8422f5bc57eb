"""SQLite 数据访问层。"""
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    orig_path TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    share_token TEXT UNIQUE,
    published_path TEXT,
    published_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS drafts (
    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    boxes_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db():
    """FastAPI 依赖：每请求一个连接，正常结束自动提交。"""
    with connect() as conn:
        yield conn


# ---------- users / sessions ----------

def create_user(conn, username: str, password_hash: str) -> int:
    cur = conn.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, password_hash),
    )
    return cur.lastrowid


def user_by_name(conn, username: str):
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def user_by_id(conn, user_id: int):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def count_users(conn) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def create_session(conn, token: str, user_id: int, ttl_hours: int) -> None:
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, time.time() + ttl_hours * 3600),
    )


def session_user(conn, token: str):
    row = conn.execute(
        "SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)
    ).fetchone()
    if not row:
        return None
    if row["expires_at"] < time.time():
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return None
    return user_by_id(conn, row["user_id"])


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


# ---------- documents ----------

def create_document(conn, doc_id: str, owner_id: int, filename: str, orig_path: str, page_count: int) -> None:
    conn.execute(
        "INSERT INTO documents (id, owner_id, filename, orig_path, page_count) VALUES (?, ?, ?, ?, ?)",
        (doc_id, owner_id, filename, orig_path, page_count),
    )


def list_documents(conn, owner_id: int):
    return conn.execute(
        """SELECT d.*, (SELECT boxes_json FROM drafts WHERE document_id = d.id) AS boxes_json
           FROM documents d WHERE d.owner_id = ? ORDER BY d.created_at DESC""",
        (owner_id,),
    ).fetchall()


def get_document(conn, doc_id: str):
    return conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()


def get_document_by_share(conn, token: str):
    return conn.execute("SELECT * FROM documents WHERE share_token = ?", (token,)).fetchone()


def touch_document(conn, doc_id: str) -> None:
    conn.execute("UPDATE documents SET updated_at = datetime('now') WHERE id = ?", (doc_id,))


def mark_published(conn, doc_id: str, share_token: str, published_path: str) -> None:
    conn.execute(
        """UPDATE documents
           SET status = 'published', share_token = ?, published_path = ?,
               published_at = datetime('now'), updated_at = datetime('now')
           WHERE id = ?""",
        (share_token, published_path, doc_id),
    )


def delete_document(conn, doc_id: str) -> None:
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


# ---------- drafts ----------

def get_draft(conn, doc_id: str):
    return conn.execute("SELECT * FROM drafts WHERE document_id = ?", (doc_id,)).fetchone()


def save_draft(conn, doc_id: str, boxes_json: str) -> None:
    conn.execute(
        """INSERT INTO drafts (document_id, boxes_json, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(document_id) DO UPDATE SET boxes_json = excluded.boxes_json,
                                                  updated_at = datetime('now')""",
        (doc_id, boxes_json),
    )
