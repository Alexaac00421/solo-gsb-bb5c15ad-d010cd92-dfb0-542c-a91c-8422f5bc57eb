from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlite3 import Connection

from .. import db
from ..deps import get_conn

router = APIRouter(prefix="/api/share", tags=["share"])


def _shared_doc(token: str, conn: Connection):
    if not token or len(token) > 128:
        raise HTTPException(status_code=404, detail="分享链接无效")
    doc = db.get_document_by_share(conn, token)
    if not doc or doc["status"] != "published" or not doc["published_path"]:
        raise HTTPException(status_code=404, detail="分享链接无效或文档未发布")
    return doc


@router.get("/{token}/info")
def share_info(token: str, conn: Connection = Depends(get_conn)):
    doc = _shared_doc(token, conn)
    return {"filename": doc["filename"], "published_at": doc["published_at"]}


@router.get("/{token}")
def share_download(token: str, conn: Connection = Depends(get_conn)):
    """分享链接只能下载已发布的脱敏版，原件不对其暴露。"""
    doc = _shared_doc(token, conn)
    return FileResponse(
        doc["published_path"],
        media_type="application/pdf",
        filename=f"redacted-{doc['filename']}",
    )
