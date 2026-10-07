import os
import sys
import tempfile
from pathlib import Path

# 必须在导入 app 之前设置数据目录，避免污染真实数据
_TMP_DATA = tempfile.mkdtemp(prefix="redact-test-")
os.environ["DATA_DIR"] = _TMP_DATA

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c



@pytest.fixture(autouse=True)
def fresh_data_dir(tmp_path, monkeypatch):
    """每个测试用例使用独立的数据目录与数据库。"""
    from app import config, db

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(config, "PUBLISHED_DIR", tmp_path / "published")
    config.ensure_dirs()
    db.init_db()


@pytest.fixture()
def sample_pdf(tmp_path) -> str:
    """两页 PDF：第 2 页旋转 90°；含文字、图片、注释、链接、附件、元数据、书签。"""
    path = tmp_path / "sample.pdf"
    doc = pymupdf.open()

    p0 = doc.new_page(width=612, height=792)
    p0.insert_text((72, 100), "SECRET-PAGE0-TEXT", fontsize=14)
    p0.insert_text((72, 150), "public text page0", fontsize=14)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 60))
    pix.set_rect(pix.irect, (200, 30, 30))
    p0.insert_image(pymupdf.Rect(72, 200, 132, 260), pixmap=pix)
    p0.add_text_annot((72, 400), "sticky-note-secret")
    p0.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 450, 200, 470), "uri": "https://secret.example.com"})

    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((72, 100), "SECRET-PAGE1-ROTATED", fontsize=14)
    p1.insert_text((72, 700), "public text page1", fontsize=14)
    p1.set_rotation(90)

    doc.embfile_add("payload.txt", b"attached-secret-bytes")
    doc.set_metadata({"title": "SecretTitle", "author": "SecretAuthor", "producer": "SecretProducer"})
    doc.set_toc([[1, "SecretBookmark", 1]])
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture()
def encrypted_pdf(tmp_path) -> str:
    path = tmp_path / "enc.pdf"
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "secret")
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="own")
    doc.close()
    return str(path)


@pytest.fixture()
def empty_pw_encrypted_pdf(tmp_path) -> str:
    path = tmp_path / "enc2.pdf"
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="own")
    doc.close()
    return str(path)


@pytest.fixture()
def corrupt_pdf(tmp_path) -> str:
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4 broken" + b"\x00\xff" * 200)
    return str(path)
