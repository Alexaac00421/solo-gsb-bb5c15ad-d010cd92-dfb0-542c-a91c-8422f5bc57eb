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
    current_version INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS drafts (
    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    boxes_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- 每次成功发布形成一行：版本号在同一文档内严格递增、不可改
CREATE TABLE IF NOT EXISTS versions (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    boxes_json TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (document_id, version)
);
-- 指定版本的独立分享链接；与文档级链接不同，永远固定在该版本
CREATE TABLE IF NOT EXISTS version_shares (
    token TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (document_id, version),
    FOREIGN KEY (document_id, version) REFERENCES versions(document_id, version) ON DELETE CASCADE
);
"""


def _migrate(conn) -> None:
    """兼容旧库：补齐列并把旧的单文件发布补登记为 v1。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
    if "current_version" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN current_version INTEGER")

    have_versions = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='versions'"
    ).fetchone()
    if not have_versions:
        return
    legacy = conn.execute(
        """SELECT id, published_path, published_at FROM documents
           WHERE status = 'published' AND published_path IS NOT NULL
             AND id NOT IN (SELECT document_id FROM versions)"""
    ).fetchall()
    for row in legacy:
        boxes_json = conn.execute(
            "SELECT boxes_json FROM drafts WHERE document_id = ?", (row["id"],)
        ).fetchone()
        conn.execute(
            """INSERT INTO versions (document_id, version, file_path, boxes_json, published_at)
               VALUES (?, 1, ?, ?, ?)""",
            (row["id"], row["published_path"], boxes_json["boxes_json"] if boxes_json else "[]",
             row["published_at"]),
        )
        conn.execute(
            "UPDATE documents SET current_version = 1 WHERE id = ? AND current_version IS NULL",
            (row["id"],),
        )


def init_db() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


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


def mark_published(conn, doc_id: str, share_token: str, version: int,
                   published_path: str, published_at: str) -> None:
    """在已取得版本号的同一事务内提交：新增版本并把“当前版本”指向它。

    仅在版本文件已成功落盘后调用。published_at/published_path 同步冗余到
    documents，保证旧链接与旧字段的行为兼容。
    """
    conn.execute(
        """UPDATE documents
           SET status = 'published', share_token = ?, published_path = ?,
               published_at = ?, current_version = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (share_token, published_path, published_at, version, doc_id),
    )


def add_version(conn, doc_id: str, version: int, file_path: str, boxes_json: str) -> str:
    """写入版本行，返回数据库生成的发布时间（同一事务读取，保证与调用方一致）。"""
    cur = conn.execute(
        """INSERT INTO versions (document_id, version, file_path, boxes_json)
           VALUES (?, ?, ?, ?)""",
        (doc_id, version, file_path, boxes_json),
    )
    published_at = conn.execute(
        "SELECT published_at FROM versions WHERE document_id = ? AND version = ?",
        (doc_id, version),
    ).fetchone()["published_at"]
    return published_at


def next_version_number(conn, doc_id: str) -> int:
    """必须在 BEGIN IMMEDIATE 事务内调用：并发发布会串行取到不同的递增版本号。"""
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM versions WHERE document_id = ?",
        (doc_id,),
    ).fetchone()
    return row["v"]


def list_versions(conn, doc_id: str):
    return conn.execute(
        """SELECT v.document_id, v.version, v.file_path, v.published_at,
                  (SELECT token FROM version_shares s
                    WHERE s.document_id = v.document_id AND s.version = v.version) AS share_token
           FROM versions v WHERE v.document_id = ?
           ORDER BY v.version DESC""",
        (doc_id,),
    ).fetchall()


def get_version(conn, doc_id: str, version: int):
    return conn.execute(
        "SELECT * FROM versions WHERE document_id = ? AND version = ?",
        (doc_id, version),
    ).fetchone()


def get_version_share(conn, doc_id: str, version: int):
    return conn.execute(
        "SELECT * FROM version_shares WHERE document_id = ? AND version = ?",
        (doc_id, version),
    ).fetchone()


def create_version_share(conn, token: str, doc_id: str, version: int) -> None:
    conn.execute(
        "INSERT INTO version_shares (token, document_id, version) VALUES (?, ?, ?)",
        (token, doc_id, version),
    )


def get_version_share_by_token(conn, token: str):
    return conn.execute(
        "SELECT * FROM version_shares WHERE token = ?", (token,)
    ).fetchone()


def version_file_paths(conn, doc_id: str) -> list[str]:
    """返回该文档所有版本文件路径（供路由层删除磁盘文件）。"""
    rows = conn.execute(
        "SELECT file_path FROM versions WHERE document_id = ?", (doc_id,)
    ).fetchall()
    return [r["file_path"] for r in rows]


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
