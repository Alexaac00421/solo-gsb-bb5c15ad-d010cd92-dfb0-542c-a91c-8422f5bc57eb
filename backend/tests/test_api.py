import io
import json

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


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


class TestVersions:
    def _publish(self, client, doc_id, page=0, x0=60):
        boxes = {"boxes": [{"page": page, "x0": x0, "y0": 670, "x1": x0 + 240, "y1": 710}]}
        r = client.post(f"/api/documents/{doc_id}/publish", json=boxes)
        assert r.status_code == 200, r.text
        return r, boxes["boxes"]

    def test_incremental_immutable_versions(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        r1, b1 = self._publish(client, doc_id, page=0)
        assert r1.json()["current_version_no"] == 1
        r2, b2 = self._publish(client, doc_id, page=1)
        assert r2.json()["current_version_no"] == 2
        # 文档主分享链接两次发布保持不变
        assert r1.json()["share_token"] == r2.json()["share_token"]
        main_token = r2.json()["share_token"]

        listing = client.get(f"/api/documents/{doc_id}/versions").json()
        assert listing["current_version_no"] == 2
        assert [v["version_no"] for v in listing["versions"]] == [2, 1]
        assert listing["versions"][0]["is_current"] is True
        assert listing["versions"][1]["is_current"] is False

        # 选区快照按版本留存
        d1 = client.get(f"/api/documents/{doc_id}/versions/1").json()
        d2 = client.get(f"/api/documents/{doc_id}/versions/2").json()
        assert d1["boxes"] == b1 and d2["boxes"] == b2
        assert d1["boxes"] != d2["boxes"]

        # 主链接/已发布下载始终指向最新成功版
        anon = TestClient(app)
        latest = anon.get(f"/api/share/{main_token}").content
        p = pymupdf.open(stream=latest, filetype="pdf")
        assert "SECRET-PAGE1-ROTATED" not in p[1].get_text()  # v2 遮盖第 2 页
        assert "SECRET-PAGE0-TEXT" in p[0].get_text()        # v2 没遮盖第 1 页
        p.close()
        assert client.get(f"/api/documents/{doc_id}/published").content == latest

        # 创建者可下载任一历史版本，旧版内容不变
        v1_bytes = client.get(f"/api/documents/{doc_id}/versions/1/file").content
        p = pymupdf.open(stream=v1_bytes, filetype="pdf")
        assert "SECRET-PAGE0-TEXT" not in p[0].get_text()    # v1 遮盖第 1 页
        assert "SECRET-PAGE1-ROTATED" in p[1].get_text()
        p.close()

    def test_pinned_version_share_is_fixed(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        self._publish(client, doc_id, page=0)
        self._publish(client, doc_id, page=1)

        # 为第 1 版取得独立分享链接（无需登录即可下载）
        r = client.post(f"/api/documents/{doc_id}/versions/1/share")
        assert r.status_code == 200
        pin_token = r.json()["share_token"]
        assert r.json()["version_no"] == 1
        # 重复签发返回同一链接
        assert client.post(f"/api/documents/{doc_id}/versions/1/share").json()["share_token"] == pin_token
        # 固定链接与主链接是不同令牌
        main_token = client.get(f"/api/documents/{doc_id}").json()["share_token"]
        assert pin_token != main_token

        anon = TestClient(app)
        v1_bytes = anon.get(f"/api/share/{pin_token}").content
        info = anon.get(f"/api/share/{pin_token}/info").json()
        assert info["version_no"] == 1 and info["pinned"] is True
        main_info = anon.get(f"/api/share/{main_token}/info").json()
        assert main_info["version_no"] == 2 and main_info["pinned"] is False

        # 再发第 3 版：固定链接内容不变，主链接跟随到第 3 版
        self._publish(client, doc_id, page=0, x0=100)
        assert anon.get(f"/api/share/{pin_token}").content == v1_bytes
        assert anon.get(f"/api/share/{main_token}").content != v1_bytes
        assert anon.get(f"/api/share/{main_token}/info").json()["version_no"] == 3

    def test_failed_publish_leaves_no_half_version(self, client, sample_pdf, monkeypatch):
        from app import pdfcore
        from app.routes import documents as doc_routes

        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        self._publish(client, doc_id, page=0)
        main_token = client.get(f"/api/documents/{doc_id}").json()["share_token"]
        first = client.get(f"/api/documents/{doc_id}/published").content

        # 导出失败：不产生新版本，当前版本与链接不变
        def boom(*a, **k):
            raise pdfcore.PdfExportError("生成脱敏 PDF 失败")

        monkeypatch.setattr(doc_routes.pdfcore, "build_redacted_pdf", boom)
        r = client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [{"page": 1, "x0": 60, "y0": 670, "x1": 300, "y1": 710}]},
        )
        assert r.status_code == 500
        versions = client.get(f"/api/documents/{doc_id}/versions").json()["versions"]
        assert [v["version_no"] for v in versions] == [1]
        doc = client.get(f"/api/documents/{doc_id}").json()
        assert doc["current_version_no"] == 1
        assert TestClient(app).get(f"/api/share/{main_token}").content == first

        # 选区校验失败：同样不留版本
        monkeypatch.undo()
        r = client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [{"page": 0, "x0": 0, "y0": 0, "x1": 99999, "y1": 10}]},
        )
        assert r.status_code == 400
        assert [v["version_no"] for v in client.get(f"/api/documents/{doc_id}/versions").json()["versions"]] == [1]

    def test_missing_version_file_errors_not_fallback(self, client, sample_pdf):
        import os
        from pathlib import Path
        from app import config

        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        self._publish(client, doc_id, page=0)
        self._publish(client, doc_id, page=1)
        pin = client.post(f"/api/documents/{doc_id}/versions/1/share").json()["share_token"]

        v1 = next(p for p in Path(config.PUBLISHED_DIR).glob(f"{doc_id}-v1.pdf"))
        v1.unlink()
        os.sync()

        # 持第 1 版固定链接：文件缺失报错，不回退到第 2 版
        assert TestClient(app).get(f"/api/share/{pin}").status_code == 500
        # 创建者下载该版同样报错
        assert client.get(f"/api/documents/{doc_id}/versions/1/file").status_code == 500
        # 无法为缺文件的版本签发链接
        assert client.post(f"/api/documents/{doc_id}/versions/1/share").status_code == 500
        # 第 2 版（当前版本）仍正常
        assert client.get(f"/api/documents/{doc_id}/versions/2/file").status_code == 200

    def test_write_failure_leaves_no_half_version(self, client, sample_pdf, monkeypatch):
        from app.routes import documents as doc_routes

        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        self._publish(client, doc_id, page=0)
        first = client.get(f"/api/documents/{doc_id}/published").content

        real_replace = doc_routes.os.replace

        def fail_once(src, dst):
            if str(dst).endswith(".pdf") and "-v2" in str(dst):
                raise OSError("disk full")
            return real_replace(src, dst)

        monkeypatch.setattr(doc_routes.os, "replace", fail_once)
        r = client.post(
            f"/api/documents/{doc_id}/publish",
            json={"boxes": [{"page": 1, "x0": 60, "y0": 670, "x1": 300, "y1": 710}]},
        )
        assert r.status_code == 500
        monkeypatch.undo()
        # 无 v2 版本，当前仍为 v1，链接内容不变
        versions = client.get(f"/api/documents/{doc_id}/versions").json()
        assert [v["version_no"] for v in versions["versions"]] == [1]
        assert client.get(f"/api/documents/{doc_id}").json()["current_version_no"] == 1
        assert client.get(f"/api/documents/{doc_id}/published").content == first

    def test_other_account_cannot_access_versions(self, client, sample_pdf):
        _register(client, "alice", "secret123")
        doc_id = _upload(client, sample_pdf).json()["id"]
        self._publish(client, doc_id, page=0)
        pin = client.post(f"/api/documents/{doc_id}/versions/1/share").json()["share_token"]
        client.post("/api/auth/logout")
        _register(client, "bob", "secret456")
        for method, path in [
            ("get", f"/api/documents/{doc_id}/versions"),
            ("get", f"/api/documents/{doc_id}/versions/1"),
            ("get", f"/api/documents/{doc_id}/versions/1/file"),
            ("post", f"/api/documents/{doc_id}/versions/1/share"),
        ]:
            r = getattr(client, method)(path)
            assert r.status_code == 404, (method, path, r.status_code)
        # 但固定版本链接任何人可下载脱敏成品
        assert TestClient(app).get(f"/api/share/{pin}").status_code == 200

    def test_delete_document_invalidates_all_links_and_files(self, client, sample_pdf):
        from pathlib import Path
        from app import config

        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        self._publish(client, doc_id, page=0)
        self._publish(client, doc_id, page=1)
        main_token = client.get(f"/api/documents/{doc_id}").json()["share_token"]
        pin1 = client.post(f"/api/documents/{doc_id}/versions/1/share").json()["share_token"]
        pin2 = client.post(f"/api/documents/{doc_id}/versions/2/share").json()["share_token"]

        files = list(Path(config.PUBLISHED_DIR).glob(f"{doc_id}-v*.pdf"))
        assert len(files) == 2
        assert client.delete(f"/api/documents/{doc_id}").status_code == 200

        anon = TestClient(app)
        for token in (main_token, pin1, pin2):
            assert anon.get(f"/api/share/{token}").status_code == 404
            assert anon.get(f"/api/share/{token}/info").status_code == 404
        assert all(not p.exists() for p in files)

    def test_concurrent_publish_gets_distinct_versions(self, client, sample_pdf):
        import threading

        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        box = {"boxes": [{"page": 0, "x0": 60, "y0": 670, "x1": 300, "y1": 710}]}
        results = []
        errors = []
        lock = threading.Lock()

        def fire():
            c = TestClient(app)
            r = c.post("/api/auth/login", json={"username": "alice", "password": "secret123"})
            assert r.status_code == 200
            resp = c.post(f"/api/documents/{doc_id}/publish", json=box)
            with lock:
                if resp.status_code == 200:
                    results.append(resp.json()["current_version_no"])
                else:
                    errors.append(resp.status_code)

        threads = [threading.Thread(target=fire) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        # 成功提交的版本号互不相同
        assert not errors, errors
        assert sorted(results) == [1, 2, 3, 4]
        listing = client.get(f"/api/documents/{doc_id}/versions").json()
        assert listing["current_version_no"] == max(results)
        assert len(listing["versions"]) == 4

    def test_publish_response_keeps_compatible_fields(self, client, sample_pdf):
        _register(client)
        doc_id = _upload(client, sample_pdf).json()["id"]
        r, _ = self._publish(client, doc_id)
        body = r.json()
        for field in ("id", "filename", "page_count", "status", "share_token",
                      "published_at", "created_at", "updated_at", "current_version_no"):
            assert field in body
        assert body["status"] == "published"
