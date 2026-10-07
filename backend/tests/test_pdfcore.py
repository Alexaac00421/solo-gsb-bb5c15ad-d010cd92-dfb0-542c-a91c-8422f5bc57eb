import pymupdf
import pytest

from app import pdfcore


def _box(page, x0, y0, x1, y1):
    return {"page": page, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


class TestOpenPdf:
    def test_rejects_encrypted(self, encrypted_pdf):
        with pytest.raises(pdfcore.PdfValidationError, match="加密"):
            pdfcore.open_pdf(encrypted_pdf)

    def test_rejects_empty_password_encrypted(self, empty_pw_encrypted_pdf):
        with pytest.raises(pdfcore.PdfValidationError, match="加密"):
            pdfcore.open_pdf(empty_pw_encrypted_pdf)

    def test_rejects_corrupt(self, corrupt_pdf):
        with pytest.raises(pdfcore.PdfValidationError, match="损坏"):
            pdfcore.open_pdf(corrupt_pdf)

    def test_rejects_non_pdf(self, tmp_path):
        p = tmp_path / "x.pdf"
        p.write_bytes(b"not a pdf at all")
        with pytest.raises(pdfcore.PdfValidationError):
            pdfcore.open_pdf(str(p))

    def test_opens_valid(self, sample_pdf):
        doc = pdfcore.open_pdf(sample_pdf)
        assert doc.page_count == 2
        doc.close()


class TestValidateBoxes:
    def test_out_of_bounds_rejected(self, sample_pdf):
        doc = pdfcore.open_pdf(sample_pdf)
        with pytest.raises(pdfcore.PdfValidationError, match="边界"):
            pdfcore.validate_boxes(doc, [_box(0, -10, 700, 100, 200)])
        with pytest.raises(pdfcore.PdfValidationError, match="边界"):
            pdfcore.validate_boxes(doc, [_box(0, 0, 0, 612, 800)])
        doc.close()

    def test_bad_page_rejected(self, sample_pdf):
        doc = pdfcore.open_pdf(sample_pdf)
        with pytest.raises(pdfcore.PdfValidationError, match="页码"):
            pdfcore.validate_boxes(doc, [_box(5, 0, 0, 100, 100)])
        with pytest.raises(pdfcore.PdfValidationError, match="页码"):
            pdfcore.validate_boxes(doc, [_box(-1, 0, 0, 100, 100)])
        doc.close()

    def test_degenerate_rejected(self, sample_pdf):
        doc = pdfcore.open_pdf(sample_pdf)
        with pytest.raises(pdfcore.PdfValidationError, match="过小"):
            pdfcore.validate_boxes(doc, [_box(0, 100, 100, 100.1, 100.1)])
        doc.close()

    def test_corners_normalized(self, sample_pdf):
        doc = pdfcore.open_pdf(sample_pdf)
        boxes = pdfcore.validate_boxes(doc, [_box(0, 200, 150, 72, 90)])
        assert boxes[0].x0 == 72 and boxes[0].x1 == 200
        assert boxes[0].y0 == 90 and boxes[0].y1 == 150
        doc.close()

    def test_non_dict_rejected(self, sample_pdf):
        doc = pdfcore.open_pdf(sample_pdf)
        with pytest.raises(pdfcore.PdfValidationError):
            pdfcore.validate_boxes(doc, ["nonsense"])
        doc.close()


class TestBuildRedactedPdf:
    def test_text_removed_inside_kept_outside(self, sample_pdf):
        # 用户空间坐标（y 向上）：覆盖第 1 页顶部文字区域
        boxes = [_box(0, 60, 792 - 115, 300, 792 - 85)]
        data = pdfcore.build_redacted_pdf(sample_pdf, boxes)
        out = pymupdf.open(stream=data, filetype="pdf")
        text = out[0].get_text()
        assert "SECRET-PAGE0-TEXT" not in text
        assert "public text page0" in text  # 区域外文字仍可搜索
        out.close()

    def test_rotated_page_redaction(self, sample_pdf):
        # 第 2 页旋转 90°，用户空间坐标不变，遮盖应落在正确位置
        boxes = [_box(1, 60, 792 - 115, 400, 792 - 85)]
        data = pdfcore.build_redacted_pdf(sample_pdf, boxes)
        out = pymupdf.open(stream=data, filetype="pdf")
        page = out[1]
        assert page.rotation == 90
        text = page.get_text()
        assert "SECRET-PAGE1-ROTATED" not in text
        assert "public text page1" in text
        out.close()

    def test_page_geometry_and_order_preserved(self, sample_pdf):
        boxes = [_box(0, 60, 670, 300, 700)]
        data = pdfcore.build_redacted_pdf(sample_pdf, boxes)
        out = pymupdf.open(stream=data, filetype="pdf")
        assert out.page_count == 2
        assert out[0].mediabox == pymupdf.Rect(0, 0, 612, 792)
        assert out[1].mediabox == pymupdf.Rect(0, 0, 612, 792)
        assert out[1].rotation == 90
        assert "page0" in out[0].get_text()
        assert "page1" in out[1].get_text()
        out.close()

    def test_image_removed(self, sample_pdf):
        boxes = [_box(0, 60, 792 - 270, 150, 792 - 190)]  # 覆盖图片区域
        data = pdfcore.build_redacted_pdf(sample_pdf, boxes)
        out = pymupdf.open(stream=data, filetype="pdf")
        assert out[0].get_images(full=True) == []
        out.close()

    def test_no_metadata_annots_attachments_links(self, sample_pdf):
        data = pdfcore.build_redacted_pdf(sample_pdf, [])
        out = pymupdf.open(stream=data, filetype="pdf")
        meta = out.metadata
        assert all(not meta[k] for k in ("title", "author", "producer", "creator", "subject", "keywords"))
        assert out.embfile_names() == []
        assert out.get_toc() == []
        for page in out:
            assert list(page.annots()) == []
            assert page.get_links() == []
        out.close()

    def test_secrets_not_in_raw_bytes(self, sample_pdf):
        boxes = [_box(0, 60, 670, 300, 700)]
        data = pdfcore.build_redacted_pdf(sample_pdf, boxes)
        for needle in (
            b"SECRET-PAGE0-TEXT",
            b"SecretTitle",
            b"SecretAuthor",
            b"SecretProducer",
            b"sticky-note-secret",
            b"attached-secret-bytes",
            b"SecretBookmark",
            b"secret.example.com",
        ):
            assert needle not in data, f"泄漏: {needle}"

    def test_out_of_bounds_raises_and_no_output(self, sample_pdf):
        with pytest.raises(pdfcore.PdfValidationError):
            pdfcore.build_redacted_pdf(sample_pdf, [_box(0, -50, 0, 100, 100)])

    def test_encrypted_raises(self, encrypted_pdf):
        with pytest.raises(pdfcore.PdfValidationError):
            pdfcore.build_redacted_pdf(encrypted_pdf, [])
