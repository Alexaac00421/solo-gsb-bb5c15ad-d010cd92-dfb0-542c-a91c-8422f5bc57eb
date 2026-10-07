import json
import os
import uuid
from pathlib import Path
from sqlite3 import Connection

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .. import config, db, pdfcore, security
from ..deps import get_conn, current_user, owned_document

router = APIRouter(prefix="/api/documents", tags=["documents"])


class BoxesPayload(BaseModel):
    boxes: list[dict]


def _doc_json(row, boxes: list | None = None) -> dict:
    out = {
        "id": row["id"],
        "filename": row["filename"],
        "page_count": row["page_count"],
        "status": row["status"],
        "share_token": row["share_token"],
        "published_at": row["published_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if boxes is not None:
        out["boxes"] = boxes
    return out


def _draft_boxes(conn: Connection, doc_id: str) -> list:
    draft = db.get_draft(conn, doc_id)
    if not draft:
        return []
    try:
        boxes = json.loads(draft["boxes_json"])
        return boxes if isinstance(boxes, list) else []
    except (ValueError, TypeError):
        return []


@router.get("")
def list_documents(user=Depends(current_user), conn: Connection = Depends(get_conn)):
    rows = db.list_documents(conn, user["id"])
    result = []
    for row in rows:
        box_count = 0
        if row["boxes_json"]:
            try:
                box_count = len(json.loads(row["boxes_json"]))
            except (ValueError, TypeError):
                box_count = 0
        item = _doc_json(row)
        item["box_count"] = box_count
        result.append(item)
    return result


@router.post("", status_code=201)
def upload_document(
    file: UploadFile,
    user=Depends(current_user),
    conn: Connection = Depends(get_conn),
):
    filename = Path(file.filename or "document.pdf").name or "document.pdf"
    doc_id = uuid.uuid4().hex
    tmp_path = config.FILES_DIR / f"{doc_id}.tmp"
    final_path = config.FILES_DIR / f"{doc_id}.pdf"

    # 流式写入临时文件并限制大小；校验通过后才转为正式文件
    size = 0
    try:
        with open(tmp_path, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > config.MAX_UPLOAD_MB * 1024 * 1024:
                    raise pdfcore.PdfValidationError(
                        f"文件超过大小限制（{config.MAX_UPLOAD_MB} MB）"
                    )
                out.write(chunk)
        doc = pdfcore.open_pdf(tmp_path)  # 加密/损坏在此被拒绝
        page_count = doc.page_count
        doc.close()
    except pdfcore.PdfValidationError:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="上传被拒绝：文件加密、损坏或超限，未保存任何数据") from None
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="上传失败：无法读取文件") from None

    os.replace(tmp_path, final_path)
    db.create_document(conn, doc_id, user["id"], filename, str(final_path), page_count)
    row = db.get_document(conn, doc_id)
    return _doc_json(row, boxes=[])


@router.get("/{doc_id}")
def get_document(doc=Depends(owned_document), conn: Connection = Depends(get_conn)):
    return _doc_json(doc, boxes=_draft_boxes(conn, doc["id"]))


@router.get("/{doc_id}/file")
def download_original(doc=Depends(owned_document)):
    """仅创建者可下载原件（owned_document 已做归属校验）。"""
    return FileResponse(doc["orig_path"], media_type="application/pdf", filename=doc["filename"])


@router.put("/{doc_id}/draft")
def save_draft(
    body: BoxesPayload,
    doc=Depends(owned_document),
    conn: Connection = Depends(get_conn),
):
    # 先基于真实 PDF 校验；越界/非法选区直接 400，已有草稿不被覆盖
    pdf = pdfcore.open_pdf(doc["orig_path"])
    try:
        boxes = pdfcore.validate_boxes(pdf, body.boxes)
    except pdfcore.PdfValidationError as exc:
        raise HTTPException(status_code=400, detail=f"草稿被拒绝：{exc}（已有草稿未受影响）") from None
    finally:
        pdf.close()
    db.save_draft(conn, doc["id"], json.dumps([b.as_dict() for b in boxes]))
    db.touch_document(conn, doc["id"])
    return {"ok": True, "box_count": len(boxes)}


@router.post("/{doc_id}/preview")
def preview(body: BoxesPayload, doc=Depends(owned_document)):
    """用提交的选区即时生成脱敏预览，不落盘、不写草稿。"""
    try:
        data = pdfcore.build_redacted_pdf(doc["orig_path"], body.boxes)
    except pdfcore.PdfValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except pdfcore.PdfExportError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return Response(content=data, media_type="application/pdf")


@router.post("/{doc_id}/publish")
def publish(
    body: BoxesPayload,
    doc=Depends(owned_document),
    conn: Connection = Depends(get_conn),
):
    doc_id = doc["id"]
    # 1) 校验选区 —— 失败则 400，已有草稿与原发布版均不受影响
    pdf = pdfcore.open_pdf(doc["orig_path"])
    try:
        boxes = pdfcore.validate_boxes(pdf, body.boxes)
    except pdfcore.PdfValidationError as exc:
        raise HTTPException(status_code=400, detail=f"发布被拒绝：{exc}（已有草稿未受影响）") from None
    finally:
        pdf.close()

    # 2) 校验通过，先保存草稿并立即提交——即使后续导出失败，这份草稿也已持久化
    db.save_draft(conn, doc_id, json.dumps([b.as_dict() for b in boxes]))
    conn.commit()

    # 3) 生成脱敏 PDF —— 失败则 500，原件与已存草稿保持不动
    try:
        data = pdfcore.build_redacted_pdf(doc["orig_path"], body.boxes)
    except pdfcore.PdfValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except pdfcore.PdfExportError as exc:
        raise HTTPException(status_code=500, detail=f"{exc}（原件与草稿已保留）") from None

    # 4) 临时文件 + 原子替换，避免半成品覆盖已发布版本
    published_path = config.PUBLISHED_DIR / f"{doc_id}.pdf"
    tmp_path = config.PUBLISHED_DIR / f"{doc_id}.tmp"
    try:
        with open(tmp_path, "wb") as out:
            out.write(data)
        os.replace(tmp_path, published_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="写入发布文件失败（原件与草稿已保留）") from None

    share_token = doc["share_token"] or security.new_token()
    db.mark_published(conn, doc_id, share_token, str(published_path))
    row = db.get_document(conn, doc_id)
    return _doc_json(row)


@router.get("/{doc_id}/published")
def download_published(doc=Depends(owned_document)):
    if doc["status"] != "published" or not doc["published_path"]:
        raise HTTPException(status_code=404, detail="该文档尚未发布")
    return FileResponse(
        doc["published_path"],
        media_type="application/pdf",
        filename=f"redacted-{doc['filename']}",
    )


@router.delete("/{doc_id}")
def remove_document(doc=Depends(owned_document), conn: Connection = Depends(get_conn)):
    for path in (doc["orig_path"], doc["published_path"]):
        if path:
            Path(path).unlink(missing_ok=True)
    db.delete_document(conn, doc["id"])
    return {"ok": True}
