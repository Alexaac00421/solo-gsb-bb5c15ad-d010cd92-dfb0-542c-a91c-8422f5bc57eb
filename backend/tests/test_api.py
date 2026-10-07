import io
import json

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


class TestAuth:
    def test_register_login_me_logout(self, client):
        _register(client)
        r = client.get("/api/auth/me")
        assert r.status_code == 200 and r.json()["username"] == "alice"
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        assert client.get("/api/auth/me").status_code == 401
        r = client.post("/api/auth/login", json={"username": "alice", "password": "secret123"})
        assert r.status_code == 200
        r = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
        assert r.status_code == 401

    def test_duplicate_username(self, client):
        _register(client)
        client.post("/api/auth/logout")
        r = client.post("/api/auth/register", json={"username": "alice", "password": "other123"})
        assert r.status_code == 409


class TestDocuments:
    def test_upload_rejects_encrypted(self, client, encrypted_pdf):
        _register(client)
        r = _upload(client, encrypted_pdf)
        assert r.status_code == 400
        assert client.get("/api/documents").json() == []

    def test_upload_rejects_corrupt(self, client, corrupt_pdf):
        _register(client)
        r = _upload(client, corrupt_pdf)
        assert r.status_code == 400
        assert client.get("/api/documents").json() == []

    def test_upload_rejects_non_pdf(self, client):
        _register(client)
        r = client.post("/api/documents", files={"file": ("a.pdf", io.BytesIO(b"hello"), "application/pdf")})
        assert r.status_code == 400

    def test_full_flow(self, client, sample_pdf):
        _register(client)
        r = _upload(client, sample_pdf)
        assert r.status_code == 201, r.text
        doc_id = r.json()["id"]
        assert r.json()["page_count"] == 2
        assert r.json()["status"] == "draft"

        # 越界选区：拒绝且不写草稿
        bad = {"boxes": [{"page": 0, "x0": -20, "y0": 0, "x1": 100, "y1": 100}]}
        r = client.put(f"/api/documents/{doc_id}/draft", json=bad)
        assert r.status_code == 400
        assert client.get(f"/api/documents/{doc_id}").json()["boxes"] == []

        # 合法草稿
        good = {"boxes": [{"page": 0, "x0": 60, "y0": 670, "x1": 300, "y1": 710}]}
        r = client.put(f"/api/documents/{doc_id}/draft", json=good)
        assert r.status_code == 200
        boxes = client.get(f"/api/documents/{doc_id}").json()["boxes"]
        assert len(boxes) == 1

        # 预览：返回脱敏 PDF，且不改变草稿
        r = client.post(f"/api/documents/{doc_id}/preview", json=good)
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        prev = pymupdf.open(stream=r.content, filetype="pdf")
        assert "SECRET-PAGE0-TEXT" not in prev[0].get_text()
        assert "public text page0" in prev[0].get_text()
        prev.close()

        # 发布
        r = client.post(f"/api/documents/{doc_id}/publish", json=good)
        assert r.status_code == 200, r.text
        token = r.json()["share_token"]
        assert token and r.json()["status"] == "published"

        # 分享链接无需登录即可下载脱敏版
        anon = TestClient(app)
        r = anon.get(f"/api/share/{token}")
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        pub = pymupdf.open(stream=r.content, filetype="pdf")
        assert pub.page_count == 2
        assert "SECRET-PAGE0-TEXT" not in pub[0].get_text()
        assert "public text page0" in pub[0].get_text()
        assert pub.metadata["title"] == ""
        pub.close()
        r = anon.get(f"/api/share/{token}/info")
        assert r.status_code == 200 and r.json()["filename"] == "doc.pdf"

        # 分享链接拿不到原件；未登录也拿不到原件
        assert anon.get(f"/api/documents/{doc_id}/file").status_code == 401
        # 创建者可下载原件
        r = client.get(f"/api/documents/{doc_id}/file")
        assert r.status_code == 200
        orig = pymupdf.open(stream=r.content, filetype="pdf")
        assert "SECRET-PAGE0-TEXT" in orig[0].get_text()  # 原件未被修改
        orig.close()

    def test_publish_rejects_invalid_boxes_and_keeps_draft(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        good = {"boxes": [{"page": 1, "x0": 60, "y0": 670, "x1": 300, "y1": 710}]}
        assert client.put(f"/api/documents/{doc_id}/draft", json=good).status_code == 200

        bad = {"boxes": [{"page": 0, "x0": 0, "y0": 0, "x1": 9999, "y1": 100}]}
        r = client.post(f"/api/documents/{doc_id}/publish", json=bad)
        assert r.status_code == 400
        # 草稿仍是旧的，状态仍是 draft
        doc = client.get(f"/api/documents/{doc_id}").json()
        assert doc["status"] == "draft"
        assert doc["boxes"] == good["boxes"]

    def test_ownership_isolation(self, client, sample_pdf):
        _register(client, "alice", "secret123")
        doc_id = _upload(client, sample_pdf).json()["id"]
        client.post("/api/auth/logout")
        _register(client, "bob", "secret456")
        assert client.get(f"/api/documents/{doc_id}").status_code == 404
        assert client.get(f"/api/documents/{doc_id}/file").status_code == 404
        assert client.delete(f"/api/documents/{doc_id}").status_code == 404
        assert client.get("/api/documents").json() == []

    def test_delete_document(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        assert client.delete(f"/api/documents/{doc_id}").status_code == 200
        assert client.get("/api/documents").json() == []

    def test_invalid_share_token(self, client):
        assert client.get("/api/share/does-not-exist").status_code == 404
        assert client.get("/api/share/does-not-exist/info").status_code == 404

    def test_export_failure_preserves_original_draft_and_published(
        self, client, sample_pdf, monkeypatch
    ):
        from app import pdfcore
        from app.routes import documents as doc_routes

        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        with open(sample_pdf, "rb") as f:
            original_bytes = f.read()

        boxes = {"boxes": [{"page": 0, "x0": 60, "y0": 670, "x1": 300, "y1": 710}]}

        # 第一次发布成功
        r = client.post(f"/api/documents/{doc_id}/publish", json=boxes)
        assert r.status_code == 200
        token = r.json()["share_token"]
        first_published = client.get(f"/api/share/{token}").content

        # 模拟导出失败：第二次发布应 500，且草稿/原件/已发布版本全部保留
        def boom(*args, **kwargs):
            raise pdfcore.PdfExportError("生成脱敏 PDF 失败，请检查文件内容后重试")

        monkeypatch.setattr(doc_routes.pdfcore, "build_redacted_pdf", boom)
        new_boxes = {
            "boxes": [
                {"page": 0, "x0": 60, "y0": 670, "x1": 300, "y1": 710},
                {"page": 1, "x0": 60, "y0": 670, "x1": 300, "y1": 710},
            ]
        }
        r = client.post(f"/api/documents/{doc_id}/publish", json=new_boxes)
        assert r.status_code == 500

        # 草稿已保存为新选区（导出失败不清草稿）
        doc = client.get(f"/api/documents/{doc_id}").json()
        assert len(doc["boxes"]) == 2
        # 原件未被触碰
        r = client.get(f"/api/documents/{doc_id}/file")
        assert r.status_code == 200 and r.content == original_bytes
        # 已发布版本未被半成品覆盖，分享链接仍可用且内容不变
        assert client.get(f"/api/share/{token}").content == first_published
        # 状态仍为已发布（首次发布）
        assert client.get(f"/api/documents/{doc_id}").json()["status"] == "published"

    def test_preview_does_not_persist(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        boxes = {"boxes": [{"page": 0, "x0": 60, "y0": 670, "x1": 300, "y1": 710}]}
        r = client.post(f"/api/documents/{doc_id}/preview", json=boxes)
        assert r.status_code == 200
        doc = client.get(f"/api/documents/{doc_id}").json()
        assert doc["boxes"] == [] and doc["status"] == "draft"

    def test_preview_rejects_invalid_boxes(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        r = client.post(
            f"/api/documents/{doc_id}/preview",
            json={"boxes": [{"page": 0, "x0": 0, "y0": 0, "x1": 99999, "y1": 10}]},
        )
        assert r.status_code == 400
