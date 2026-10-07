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
    current_version_no INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS drafts (
    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    boxes_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS versions (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    boxes_json TEXT NOT NULL,
    pdf_path TEXT NOT NULL,
    share_token TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (document_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_versions_document ON versions(document_id, version_no);
"""


def init_db() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate_schema(conn)


def _migrate_schema(conn) -> None:
    """旧库升级：补列，并把旧版“已发布单文件”回填为不可变的第 1 版。

    documents.share_token 始终表示“当前版本链接”（重发后仍指向最新版）；
    versions.share_token 表示某一历史版本的独立固定链接。回填时第 1 版
    不另发固定链接，因此置 NULL。
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
    if "current_version_no" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN current_version_no INTEGER")

    existing = {
        (r["document_id"], r["version_no"])
        for r in conn.execute("SELECT document_id, version_no FROM versions")
    }
    old_published = conn.execute(
        """SELECT id, published_path, published_at FROM documents
           WHERE status = 'published' AND published_path IS NOT NULL"""
    ).fetchall()
    for row in old_published:
        if (row["id"], 1) in existing:
            continue
        created = row["published_at"] or _utc_now()
        conn.execute(
            """INSERT INTO versions
                   (document_id, version_no, boxes_json, pdf_path, share_token, created_at)
               VALUES (?, 1, '[]', ?, NULL, ?)""",
            (row["id"], row["published_path"], created),
        )
        conn.execute(
            "UPDATE documents SET current_version_no = 1 WHERE id = ? AND current_version_no IS NULL",
            (row["id"],),
        )


def _utc_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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


def insert_version(
    conn,
    doc_id: str,
    version_no: int,
    boxes_json: str,
    pdf_path: str,
    created_at: str,
) -> None:
    """写入一条不可变版本记录（须在持有写锁、已确定 version_no 后调用）。"""
    conn.execute(
        """INSERT INTO versions
               (document_id, version_no, boxes_json, pdf_path, share_token, created_at)
           VALUES (?, ?, ?, ?, NULL, ?)""",
        (doc_id, version_no, boxes_json, pdf_path, created_at),
    )


def mark_current_version(
    conn, doc_id: str, share_token: str, pdf_path: str, version_no: int, created_at: str
) -> None:
    """把当前版本指针指向新成功发布的版本。文档级分享链接保持不变（首发后）。"""
    conn.execute(
        """UPDATE documents
           SET status = 'published', share_token = ?, published_path = ?,
               published_at = ?, current_version_no = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (share_token, pdf_path, created_at, version_no, doc_id),
    )


def list_version_paths(conn, doc_id: str):
    return conn.execute(
        "SELECT pdf_path FROM versions WHERE document_id = ?", (doc_id,)
    ).fetchall()


def delete_document(conn, doc_id: str) -> None:
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


# ---------- versions ----------

def list_versions(conn, doc_id: str):
    return conn.execute(
        """SELECT document_id, version_no, boxes_json, pdf_path, share_token, created_at
           FROM versions WHERE document_id = ? ORDER BY version_no DESC""",
        (doc_id,),
    ).fetchall()


def get_version(conn, doc_id: str, version_no: int):
    return conn.execute(
        "SELECT * FROM versions WHERE document_id = ? AND version_no = ?",
        (doc_id, version_no),
    ).fetchone()


def get_version_by_share(conn, token: str):
    return conn.execute("SELECT * FROM versions WHERE share_token = ?", (token,)).fetchone()


def set_version_share(conn, doc_id: str, version_no: int, token: str) -> None:
    conn.execute(
        "UPDATE versions SET share_token = ? WHERE document_id = ? AND version_no = ?",
        (token, doc_id, version_no),
    )


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
