import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection, OperationalError, IntegrityError

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
        "current_version_no": row["current_version_no"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if boxes is not None:
        out["boxes"] = boxes
    return out


def _version_json(row, include_boxes: bool = False) -> dict:
    out = {
        "version_no": row["version_no"],
        "published_at": row["created_at"],
        "share_token": row["share_token"],
        "box_count": _safe_boxes_count(row["boxes_json"]),
    }
    if include_boxes:
        try:
            boxes = json.loads(row["boxes_json"])
            out["boxes"] = boxes if isinstance(boxes, list) else []
        except (ValueError, TypeError):
            out["boxes"] = []
    return out


def _safe_boxes_count(boxes_json: str) -> int:
    try:
        boxes = json.loads(boxes_json)
        return len(boxes) if isinstance(boxes, list) else 0
    except (ValueError, TypeError):
        return 0


def _draft_boxes(conn: Connection, doc_id: str) -> list:
    draft = db.get_draft(conn, doc_id)
    if not draft:
        return []
    try:
        boxes = json.loads(draft["boxes_json"])
        return boxes if isinstance(boxes, list) else []
    except (ValueError, TypeError):
        return []


def _redacted_response(path: str, filename: str):
    """返回脱敏成品；文件缺失一律报错，绝不回退到其他版本。"""
    if not path or not Path(path).is_file():
        raise HTTPException(status_code=500, detail="版本文件缺失，请联系文档创建者")
    return FileResponse(path, media_type="application/pdf", filename=f"redacted-{filename}")


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
    # 1) 校验选区 —— 失败则 400：不留版本、不改当前版本与已有链接，草稿规则不变
    pdf = pdfcore.open_pdf(doc["orig_path"])
    try:
        boxes = pdfcore.validate_boxes(pdf, body.boxes)
    except pdfcore.PdfValidationError as exc:
        raise HTTPException(status_code=400, detail=f"发布被拒绝：{exc}（已有草稿未受影响）") from None
    finally:
        pdf.close()

    # 2) 校验通过，先保存草稿并立即提交——即使后续导出失败，这份草稿也已持久化（原有规则）
    boxes_json = json.dumps([b.as_dict() for b in boxes])
    db.save_draft(conn, doc_id, boxes_json)
    conn.commit()

    # 3) 生成脱敏 PDF（耗时操作在锁外完成）—— 失败则 500，不留版本、不动当前版本
    try:
        data = pdfcore.build_redacted_pdf(doc["orig_path"], body.boxes)
    except pdfcore.PdfValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except pdfcore.PdfExportError as exc:
        raise HTTPException(status_code=500, detail=f"{exc}（原件、草稿与已发布版本已保留）") from None

    # 4) 序列化并发发布：取递增版本号、原子落盘并更新当前版本指针
    return _commit_version(conn, doc_id, boxes_json, data)


def _commit_version(conn: Connection, doc_id: str, boxes_json: str, data: bytes) -> dict:
    try:
        # IMMEDIATE：先取得写锁，保证两个并发发布会算出不同的 version_no
        conn.execute("BEGIN IMMEDIATE")
    except OperationalError:
        raise HTTPException(status_code=503, detail="当前有发布正在进行，请稍后重试") from None

    version_path = None
    try:
        fresh = db.get_document(conn, doc_id)
        if not fresh:
            raise HTTPException(status_code=404, detail="文档不存在")

        last = conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) AS m FROM versions WHERE document_id = ?",
            (doc_id,),
        ).fetchone()
        version_no = last["m"] + 1

        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        version_path = config.PUBLISHED_DIR / f"{doc_id}-v{version_no}.pdf"
        tmp_path = config.PUBLISHED_DIR / f"{doc_id}-v{version_no}.{uuid.uuid4().hex}.tmp"

        # 临时文件 + 原子替换；失败不留半成品，当前版本与已有链接保持不变
        try:
            with open(tmp_path, "wb") as out:
                out.write(data)
            os.replace(tmp_path, version_path)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="写入发布文件失败（原件、草稿与已发布版本已保留）") from None

        try:
            db.insert_version(conn, doc_id, version_no, boxes_json, str(version_path), created_at)
            # 文档级链接首发创建、重发保持不变，始终指向当前（最新成功）版本
            share_token = fresh["share_token"] or security.new_token()
            db.mark_current_version(
                conn, doc_id, share_token, str(version_path), version_no, created_at
            )
            row = db.get_document(conn, doc_id)
            conn.commit()
        except Exception:
            conn.rollback()
            version_path.unlink(missing_ok=True)
            raise
    except HTTPException:
        conn.rollback()
        raise
    except IntegrityError as exc:
        conn.rollback()
        if version_path is not None:
            version_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="并发发布冲突，请重试") from None
    except OperationalError as exc:
        conn.rollback()
        if version_path is not None:
            version_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="发布写入失败，已发布版本未受影响") from None
    except Exception:
        conn.rollback()
        if version_path is not None:
            version_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="发布失败，已发布版本未受影响") from None

    return _doc_json(row)


@router.get("/{doc_id}/published")
def download_published(doc=Depends(owned_document)):
    """创建者下载当前版本脱敏成品；始终指向最近一次成功发布，缺失则报错不回退。"""
    if doc["status"] != "published" or not doc["published_path"]:
        raise HTTPException(status_code=404, detail="该文档尚未发布")
    return _redacted_response(doc["published_path"], doc["filename"])


@router.get("/{doc_id}/versions")
def list_versions(doc=Depends(owned_document), conn: Connection = Depends(get_conn)):
    """创建者查看不可变版本记录（不含选区快照明细）。"""
    rows = db.list_versions(conn, doc["id"])
    current = doc["current_version_no"]
    return {
        "current_version_no": current,
        "versions": [
            {**_version_json(row), "is_current": row["version_no"] == current}
            for row in rows
        ],
    }


@router.get("/{doc_id}/versions/{version_no}")
def get_version(
    version_no: int, doc=Depends(owned_document), conn: Connection = Depends(get_conn)
):
    row = db.get_version(conn, doc["id"], version_no)
    if not row:
        raise HTTPException(status_code=404, detail="该版本不存在")
    out = _version_json(row, include_boxes=True)
    out["is_current"] = row["version_no"] == doc["current_version_no"]
    return out


@router.get("/{doc_id}/versions/{version_no}/file")
def download_version(
    version_no: int, doc=Depends(owned_document), conn: Connection = Depends(get_conn)
):
    """创建者下载任一历史版本脱敏成品；文件缺失报错，不回退到其他版本。"""
    row = db.get_version(conn, doc["id"], version_no)
    if not row:
        raise HTTPException(status_code=404, detail="该版本不存在")
    return _redacted_response(row["pdf_path"], f"v{version_no}-{doc['filename']}")


@router.post("/{doc_id}/versions/{version_no}/share")
def create_version_share(
    version_no: int, doc=Depends(owned_document), conn: Connection = Depends(get_conn)
):
    """为指定版本取得独立分享链接；重复获取返回同一链接。链接内容永久固定于该版本。"""
    row = db.get_version(conn, doc["id"], version_no)
    if not row:
        raise HTTPException(status_code=404, detail="该版本不存在")
    if not Path(row["pdf_path"]).is_file():
        raise HTTPException(status_code=500, detail="版本文件缺失，无法建立分享链接")
    token = row["share_token"]
    if not token:
        token = security.new_token()
        db.set_version_share(conn, doc["id"], version_no, token)
    return {"version_no": version_no, "share_token": token}


@router.delete("/{doc_id}")
def remove_document(doc=Depends(owned_document), conn: Connection = Depends(get_conn)):
    # 清理原件与全部版本成品；所有分享链接随记录级联删除而失效
    paths = [doc["orig_path"], doc["published_path"]]
    paths.extend(r["pdf_path"] for r in db.list_version_paths(conn, doc["id"]))
    for path in paths:
        if path:
            Path(path).unlink(missing_ok=True)
    db.delete_document(conn, doc["id"])
    return {"ok": True}
