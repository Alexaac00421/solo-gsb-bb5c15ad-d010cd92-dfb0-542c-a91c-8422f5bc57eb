from pathlib import Path
from sqlite3 import Connection

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .. import db
from ..deps import get_conn

router = APIRouter(prefix="/api/share", tags=["share"])


def _resolve(token: str, conn: Connection):
    """解析分享令牌。

    - 文档级令牌（documents.share_token）：始终指向最近一次成功发布的当前版本；
    - 版本级令牌（versions.share_token）：永久固定于签发时的指定版本，重发不改变其内容。
    返回 (doc_row, version_row_or_None, pdf_path, version_no)。
    """
    if not token or len(token) > 128:
        raise HTTPException(status_code=404, detail="分享链接无效")

    doc = db.get_document_by_share(conn, token)
    if doc is not None:
        if doc["status"] != "published" or not doc["published_path"]:
            raise HTTPException(status_code=404, detail="分享链接无效或文档未发布")
        version_no = doc["current_version_no"]
        version = db.get_version(conn, doc["id"], version_no) if version_no else None
        return doc, version, doc["published_path"], version_no

    version = db.get_version_by_share(conn, token)
    if version is None:
        raise HTTPException(status_code=404, detail="分享链接无效")
    doc = db.get_document(conn, version["document_id"])
    if doc is None or doc["status"] != "published":
        raise HTTPException(status_code=404, detail="分享链接无效")
    return doc, version, version["pdf_path"], version["version_no"]


def _shared_pdf(token: str, conn: Connection):
    doc, _version, pdf_path, version_no = _resolve(token, conn)
    # 版本文件缺失：报错，绝不回退到其他版本
    if not pdf_path or not Path(pdf_path).is_file():
        raise HTTPException(status_code=500, detail="该版本文件缺失，请联系文档创建者")
    return doc, pdf_path, version_no


@router.get("/{token}/info")
def share_info(token: str, conn: Connection = Depends(get_conn)):
    doc, version, _pdf_path, version_no = _resolve(token, conn)
    pinned = version is not None and version["share_token"] == token
    return {
        "filename": doc["filename"],
        "published_at": (version["created_at"] if version else doc["published_at"]),
        "version_no": version_no,
        "pinned": pinned,
    }


@router.get("/{token}")
def share_download(token: str, conn: Connection = Depends(get_conn)):
    """持链者只能下载对应版本的脱敏成品，原件与其他版本均不暴露。"""
    doc, pdf_path, version_no = _shared_pdf(token, conn)
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"redacted-v{version_no}-{doc['filename']}",
    )
