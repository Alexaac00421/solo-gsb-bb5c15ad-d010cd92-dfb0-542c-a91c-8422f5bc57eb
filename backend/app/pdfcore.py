"""PDF 校验与脱敏处理核心。

坐标约定：
- 前端（pdf.js）提交的是 PDF 默认用户空间坐标（default user space）：
  未旋转、原点为 MediaBox 左下角、y 轴向上，单位 1/72 英寸。
- PyMuPDF 的 add_redact_annot 接受"未旋转页面坐标"：
  原点为 MediaBox 左上角、y 轴向下。
- 因此转换只涉及平移与 y 轴翻转，页面旋转由 PyMuPDF 在应用遮盖时自行处理，
  保证旋转页面的选区依然落在正确位置。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pymupdf

MAX_BOXES = 2000
_EPSILON = 0.05  # 坐标浮点噪声容差（pt）
_MIN_SIZE = 0.5  # 选区最小边长（pt）


class PdfValidationError(Exception):
    """用户输入/文件校验失败（对应 HTTP 400），不得覆盖已有草稿。"""


class PdfExportError(Exception):
    """脱敏 PDF 生成失败（对应 HTTP 500），原件与已存草稿保持不动。"""


@dataclass
class Box:
    page: int  # 0 基页码
    x0: float
    y0: float
    x1: float
    y1: float

    def as_dict(self) -> dict:
        return {"page": self.page, "x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


def open_pdf(path: str | Path) -> pymupdf.Document:
    """打开并校验 PDF。加密或损坏的文件一律拒绝。"""
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:
        raise PdfValidationError("文件已损坏或不是有效的 PDF") from exc
    try:
        if not doc.is_pdf:
            raise PdfValidationError("文件已损坏或不是有效的 PDF")
        encrypted = bool(doc.needs_pass) or bool(doc.is_encrypted)
        if not encrypted:
            try:
                # 空用户密码的加密文件会被自动解密，需检查 trailer 的 /Encrypt 项
                kind, _ = doc.xref_get_key(-1, "Encrypt")
                encrypted = kind != "null"
            except Exception:
                encrypted = True
        if encrypted:
            raise PdfValidationError("不支持加密的 PDF 文件，请先解除密码保护后再上传")
        if doc.page_count < 1:
            raise PdfValidationError("PDF 不含任何页面")
        return doc
    except Exception:
        doc.close()
        raise


def validate_boxes(doc: pymupdf.Document, raw_boxes: Iterable[dict]) -> list[Box]:
    """校验并规范化选区。任何越界/非法选区都会抛出 PdfValidationError，
    调用方必须保证此时不写入草稿。"""
    if not isinstance(raw_boxes, list):
        raise PdfValidationError("选区数据格式错误")
    if len(raw_boxes) > MAX_BOXES:
        raise PdfValidationError(f"选区数量超过上限（{MAX_BOXES} 个）")

    boxes: list[Box] = []
    for idx, raw in enumerate(raw_boxes, start=1):
        if not isinstance(raw, dict):
            raise PdfValidationError(f"第 {idx} 个选区数据格式错误")
        try:
            page_no = int(raw["page"])
            x0, y0, x1, y1 = (float(raw[k]) for k in ("x0", "y0", "x1", "y1"))
        except (KeyError, TypeError, ValueError):
            raise PdfValidationError(f"第 {idx} 个选区数据格式错误") from None
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
            raise PdfValidationError(f"第 {idx} 个选区坐标无效")
        if page_no < 0 or page_no >= doc.page_count:
            raise PdfValidationError(f"第 {idx} 个选区的页码超出范围")

        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if x1 - x0 < _MIN_SIZE or y1 - y0 < _MIN_SIZE:
            raise PdfValidationError(f"第 {idx} 个选区面积过小")

        mb = doc[page_no].mediabox
        if (
            x0 < mb.x0 - _EPSILON
            or x1 > mb.x1 + _EPSILON
            or y0 < mb.y0 - _EPSILON
            or y1 > mb.y1 + _EPSILON
        ):
            raise PdfValidationError(f"第 {idx} 个选区超出第 {page_no + 1} 页的页面边界")

        # 收敛浮点噪声到页面边界内
        boxes.append(
            Box(
                page=page_no,
                x0=max(x0, mb.x0),
                y0=max(y0, mb.y0),
                x1=min(x1, mb.x1),
                y1=min(y1, mb.y1),
            )
        )
    return boxes


def _userspace_to_page_rect(page: pymupdf.Page, box: Box) -> pymupdf.Rect:
    """PDF 用户空间（y 向上）→ PyMuPDF 未旋转页面坐标（y 向下）。"""
    mb = page.mediabox
    return pymupdf.Rect(
        box.x0 - mb.x0,
        mb.y1 - box.y1,
        box.x1 - mb.x0,
        mb.y1 - box.y0,
    )


def build_redacted_pdf(src_path: str | Path, raw_boxes: list[dict]) -> bytes:
    """生成脱敏 PDF。

    - 遮盖区域内的文字/图像/矢量图形被真正移除（内容流重写），区域外文字保持可搜索；
    - 页面尺寸、旋转与顺序保持不变；
    - 输出不携带原文档的注释、链接、附件、书签与元数据。
    校验失败抛 PdfValidationError；生成过程失败抛 PdfExportError。
    """
    doc = open_pdf(src_path)
    try:
        boxes = validate_boxes(doc, raw_boxes)

        by_page: dict[int, list[Box]] = {}
        for box in boxes:
            by_page.setdefault(box.page, []).append(box)

        for page_no, page_boxes in by_page.items():
            page = doc[page_no]
            for box in page_boxes:
                page.add_redact_annot(_userspace_to_page_rect(page, box), fill=(0, 0, 0))
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )

        # 移除残留的注释与链接（遮盖注释本身已在 apply_redactions 中消耗）
        for page in doc:
            for annot in list(page.annots()):
                page.delete_annot(annot)
            for link in page.get_links():
                page.delete_link(link)

        # 清除元数据、XML 元数据、嵌入文件、隐藏文本、JS、缩略图等
        doc.scrub(
            metadata=True,
            xml_metadata=True,
            embedded_files=True,
            attached_files=True,
            hidden_text=True,
            javascript=True,
            remove_links=True,
            reset_fields=True,
            reset_responses=True,
            thumbnails=True,
            clean_pages=True,
        )
        doc.set_toc([])

        # garbage=4 回收一切未引用对象，确保被移除内容的字节不残留在输出中
        return doc.tobytes(garbage=4, deflate=True, clean=True)
    except PdfValidationError:
        raise
    except Exception as exc:
        raise PdfExportError("生成脱敏 PDF 失败，请检查文件内容后重试") from exc
    finally:
        doc.close()
