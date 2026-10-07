import threading

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app


def _register(client, username="alice", password="secret123"):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r


def _upload(client, pdf_path, filename="doc.pdf"):
    with open(pdf_path, "rb") as f:
        return client.post("/api/documents", files={"file": (filename, f, "application/pdf")})


def _box(page, x0, y0, x1, y1):
    return {"page": page, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _open(content):
    doc = pymupdf.open(stream=content, filetype="pdf")
    return doc


class TestVersions:
    def test_repeated_publish_creates_incrementing_versions(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]

        boxes_v1 = {"boxes": [_box(0, 60, 670, 300, 710)]}
        r = client.post(f"/api/documents/{doc_id}/publish", json=boxes_v1)
        assert r.status_code == 200
        assert r.json()["current_version"] == 1
        token_latest = r.json()["share_token"]

        boxes_v2 = {
            "boxes": [
                _box(0, 60, 670, 300, 710),
                _box(1, 60, 670, 300, 710),
            ]
        }
        r = client.post(f"/api/documents/{doc_id}/publish", json=boxes_v2)
        assert r.status_code == 200
        assert r.json()["current_version"] == 2
        # 文档级分享链接 token 保持不变
        assert r.json()["share_token"] == token_latest

        versions = client.get(f"/api/documents/{doc_id}/versions").json()
        assert [v["version"] for v in versions] == [2, 1]
        assert versions[0]["is_current"] is True
        assert versions[1]["is_current"] is False
        assert versions[0]["published_at"] and versions[1]["published_at"]

    def test_version_detail_keeps_box_snapshot(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        v1_boxes = [_box(0, 60, 670, 300, 710)]
        client.post(f"/api/documents/{doc_id}/publish", json={"boxes": v1_boxes})
        v2_boxes = [_box(0, 60, 670, 300, 710), _box(1, 60, 670, 300, 710)]
        client.post(f"/api/documents/{doc_id}/publish", json={"boxes": v2_boxes})

        r = client.get(f"/api/documents/{doc_id}/versions/1")
        assert r.status_code == 200
        detail = r.json()
        assert detail["version"] == 1
        assert detail["is_current"] is False
        assert len(detail["boxes"]) == 1
        assert detail["boxes"] == v1_boxes

        r = client.get(f"/api/documents/{doc_id}/versions/2")
        assert len(r.json()["boxes"]) == 2

        assert client.get(f"/api/documents/{doc_id}/versions/99").status_code == 404

    def test_owner_can_download_any_version(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710)]},
        )
        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710), _box(1, 60, 670, 300, 710)]},
        )

        v1 = client.get(f"/api/documents/{doc_id}/versions/1/download")
        assert v1.status_code == 200
        v1_doc = _open(v1.content)
        assert "SECRET-PAGE0-TEXT" not in v1_doc[0].get_text()
        # v1 未遮盖第 2 页
        assert "SECRET-PAGE1-ROTATED" in v1_doc[1].get_text()
        v1_doc.close()

        v2 = client.get(f"/api/documents/{doc_id}/versions/2/download")
        v2_doc = _open(v2.content)
        assert "SECRET-PAGE1-ROTATED" not in v2_doc[1].get_text()
        v2_doc.close()

        # /published 与文档级链接均指向最新版（v2）
        cur = client.get(f"/api/documents/{doc_id}/published")
        assert cur.status_code == 200 and cur.content == v2.content

        token = client.get(f"/api/documents/{doc_id}").json()["share_token"]
        anon = TestClient(app)
        assert anon.get(f"/api/share/{token}").content == v2.content

    def test_existing_share_link_follows_latest_version(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        r = client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710)]},
        )
        token = r.json()["share_token"]
        first = client.get(f"/api/share/{token}").content

        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710), _box(1, 60, 670, 300, 710)]},
        )
        # 同一链接现在给出第二版
        second = client.get(f"/api/share/{token}").content
        assert second != first
        latest = client.get(f"/api/documents/{doc_id}/versions/2/download").content
        assert second == latest

        info = client.get(f"/api/share/{token}/info").json()
        assert info["version"] == 2

    def test_pinned_version_share_is_immutable(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710)]},
        )
        # 为 v1 取得独立链接
        r = client.post(f"/api/documents/{doc_id}/versions/1/share")
        assert r.status_code == 200
        pinned = r.json()
        assert pinned["version"] == 1
        vtoken = pinned["share_token"]

        # 重复取得返回同一链接
        r2 = client.post(f"/api/documents/{doc_id}/versions/1/share")
        assert r2.json()["share_token"] == vtoken

        v1_bytes = client.get(f"/api/documents/{doc_id}/versions/1/download").content

        # 再次发布：版本级链接内容不变
        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710), _box(1, 60, 670, 300, 710)]},
        )
        anon = TestClient(app)
        r = anon.get(f"/api/share/v/{vtoken}")
        assert r.status_code == 200
        assert r.content == v1_bytes
        info = anon.get(f"/api/share/v/{vtoken}/info").json()
        assert info["version"] == 1

        # 版本级链接拿不到原件（无登录 → 401；且该路径根本不提供原件）
        assert anon.get(f"/api/documents/{doc_id}/file").status_code == 401

    def test_other_account_cannot_view_versions_or_create_share(self, client, sample_pdf):
        _register(client, "alice", "secret123")
        doc_id = _upload(client, sample_pdf).json()["id"]
        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710)]},
        )
        client.post("/api/auth/logout")
        _register(client, "bob", "secret456")
        assert client.get(f"/api/documents/{doc_id}/versions").status_code == 404
        assert client.get(f"/api/documents/{doc_id}/versions/1").status_code == 404
        assert client.get(f"/api/documents/{doc_id}/versions/1/download").status_code == 404
        assert client.post(f"/api/documents/{doc_id}/versions/1/share").status_code == 404

    def test_delete_document_invalidates_all_shares_and_files(self, client, sample_pdf):
        from app import config

        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710)]},
        )
        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710), _box(1, 60, 670, 300, 710)]},
        )
        latest_token = client.get(f"/api/documents/{doc_id}").json()["share_token"]
        vtoken = client.post(f"/api/documents/{doc_id}/versions/1/share").json()["share_token"]

        published_files = list(config.PUBLISHED_DIR.glob("*.pdf"))
        assert len(published_files) == 2

        assert client.delete(f"/api/documents/{doc_id}").status_code == 200

        anon = TestClient(app)
        assert anon.get(f"/api/share/{latest_token}").status_code == 404
        assert anon.get(f"/api/share/v/{vtoken}").status_code == 404
        assert list(config.PUBLISHED_DIR.glob("*.pdf")) == []

    def test_invalid_pinned_share_token(self, client):
        assert client.get("/api/share/v/does-not-exist").status_code == 404
        assert client.get("/api/share/v/does-not-exist/info").status_code == 404

    def test_failed_validation_leaves_no_half_version(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710)]},
        )
        bad = {"boxes": [_box(0, 0, 0, 9999, 100)]}
        r = client.post(f"/api/documents/{doc_id}/publish", json=bad)
        assert r.status_code == 400

        versions = client.get(f"/api/documents/{doc_id}/versions").json()
        assert [v["version"] for v in versions] == [1]
        doc = client.get(f"/api/documents/{doc_id}").json()
        assert doc["current_version"] == 1

    def test_export_failure_leaves_no_half_version_and_links_unchanged(
        self, client, sample_pdf, monkeypatch
    ):
        from app import pdfcore
        from app.routes import documents as doc_routes

        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710)]},
        )
        token = client.get(f"/api/documents/{doc_id}").json()["share_token"]
        v1_bytes = client.get(f"/api/share/{token}").content

        def boom(*args, **kwargs):
            raise pdfcore.PdfExportError("生成脱敏 PDF 失败，请检查文件内容后重试")

        monkeypatch.setattr(doc_routes.pdfcore, "build_redacted_pdf", boom)
        r = client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710), _box(1, 60, 670, 300, 710)]},
        )
        assert r.status_code == 500

        # 没有新增版本；当前版本仍是 v1；链接内容不变
        versions = client.get(f"/api/documents/{doc_id}/versions").json()
        assert [v["version"] for v in versions] == [1]
        assert client.get(f"/api/share/{token}").content == v1_bytes

    def test_missing_version_file_errors_instead_of_fallback(self, client, sample_pdf):
        from pathlib import Path
        from app import config, db

        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710)]},
        )
        client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [_box(0, 60, 670, 300, 710), _box(1, 60, 670, 300, 710)]},
        )
        latest_token = client.get(f"/api/documents/{doc_id}").json()["share_token"]
        vtoken = client.post(f"/api/documents/{doc_id}/versions/1/share").json()["share_token"]

        # 删掉 v1 的文件：下载 v1 / 固定 v1 链接必须报错，不能回退给 v2
        with db.connect() as conn:
            missing = db.get_version(conn, doc_id, 1)["file_path"]
        Path(missing).unlink()

        assert client.get(f"/api/documents/{doc_id}/versions/1/download").status_code == 500
        r = client.get(f"/api/documents/{doc_id}/published")
        assert r.status_code == 200  # 最新版 v2 文件仍在
        assert TestClient(app).get(f"/api/share/v/{vtoken}").status_code == 500

        # 当前版本文件也缺失时，文档级链接同样报错
        for p in config.PUBLISHED_DIR.glob(f"{doc_id}_*.pdf"):
            p.unlink()
        assert TestClient(app).get(f"/api/share/{latest_token}").status_code == 500

    def test_concurrent_publish_get_distinct_version_numbers(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        cookie = client.cookies.get("redact_session")

        results = []
        errors = []
        barrier = threading.Barrier(4)

        def worker(box_y):
            c = TestClient(app)
            c.cookies.set("redact_session", cookie)
            barrier.wait()
            try:
                r = c.post(
                    f"/api/documents/{doc_id}/publish",
                    json={"boxes": [_box(0, 60, box_y, 300, box_y + 30)]},
                )
                results.append((r.status_code, r.json().get("current_version") if r.status_code == 200 else None))
            except Exception as exc:  # pragma: no cover - 调试用
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(600 + i * 30,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert sorted(v for _, v in results) == [1, 2, 3, 4]
        assert all(code == 200 for code, _ in results)
        # 当前版本只指向最后成功提交的版本
        doc = client.get(f"/api/documents/{doc_id}").json()
        assert doc["current_version"] == max(v for _, v in results)
        versions = client.get(f"/api/documents/{doc_id}/versions").json()
        assert sorted(v["version"] for v in versions) == [1, 2, 3, 4]


class TestLegacyMigration:
    def test_old_database_gets_v1_and_current_version(self, tmp_path, monkeypatch):
        """旧库（无 versions 表、documents 无 current_version 列）启动后补齐。"""
        import sqlite3
        from app import config, db

        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
        monkeypatch.setattr(config, "FILES_DIR", tmp_path / "files")
        monkeypatch.setattr(config, "PUBLISHED_DIR", tmp_path / "published")
        config.ensure_dirs()
        db.init_db()  # autouse 已建出新库；这里把新表清空，手工回退为旧结构

        pub_file = config.PUBLISHED_DIR / "old.pdf"
        pub_file.write_bytes(b"%PDF-1.4 legacy")

        conn = sqlite3.connect(config.DB_PATH)
        conn.executescript(
            """
            DROP TABLE IF EXISTS version_shares;
            DROP TABLE IF EXISTS versions;
            DROP TABLE IF EXISTS drafts;
            DROP TABLE IF EXISTS documents;
            DROP TABLE IF EXISTS sessions;
            DROP TABLE IF EXISTS users;
            CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT,
                                created_at TEXT DEFAULT (datetime('now')));
            CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id INTEGER, expires_at REAL);
            CREATE TABLE documents (id TEXT PRIMARY KEY, owner_id INTEGER, filename TEXT,
                orig_path TEXT, page_count INTEGER, status TEXT DEFAULT 'draft',
                share_token TEXT UNIQUE, published_path TEXT, published_at TEXT,
                created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
            CREATE TABLE drafts (document_id TEXT PRIMARY KEY, boxes_json TEXT,
                                 updated_at TEXT DEFAULT (datetime('now')));
            INSERT INTO users (id, username, password_hash) VALUES (1, 'alice', 'x');
            INSERT INTO documents (id, owner_id, filename, orig_path, page_count, status,
                                   share_token, published_path, published_at)
                VALUES ('d1', 1, 'old.pdf', 'orig.pdf', 2, 'published', 'tok-old',
                        'PUB_PLACEHOLDER', '2026-01-01 00:00:00');
            INSERT INTO drafts (document_id, boxes_json) VALUES ('d1', '[{"page":0}]');
            """
        )
        conn.execute(
            "UPDATE documents SET published_path = ? WHERE id = 'd1'", (str(pub_file),)
        )
        conn.commit()
        conn.close()

        db.init_db()  # 触发迁移

        with db.connect() as c:
            doc = c.execute("SELECT * FROM documents WHERE id = 'd1'").fetchone()
            assert doc["current_version"] == 1
            v = c.execute("SELECT * FROM versions WHERE document_id = 'd1'").fetchall()
            assert len(v) == 1
            assert v[0]["version"] == 1
            assert v[0]["file_path"] == str(pub_file)
            assert v[0]["published_at"] == "2026-01-01 00:00:00"
            assert v[0]["boxes_json"] == '[{"page":0}]'

        # 旧分享链接经版本表解析，仍指向 v1 的旧文件（不走 TestClient lifespan）
        from app.routes.share import _current_version_share
        with db.connect() as c:
            d, ver = _current_version_share("tok-old", c)
            assert ver["version"] == 1
            assert ver["file_path"] == str(pub_file)
