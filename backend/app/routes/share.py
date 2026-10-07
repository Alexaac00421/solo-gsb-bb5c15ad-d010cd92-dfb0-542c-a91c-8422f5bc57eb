from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlite3 import Connection

from .. import db
from ..deps import get_conn

router = APIRouter(prefix="/api/share", tags=["share"])


def _require_file(path: str):
    """版本文件缺失一律 500 报错，绝不回退到其他版本。"""
    from pathlib import Path

    p = Path(path)
    if not p.is_file():
        raise HTTPException(status_code=500, detail="版本文件缺失，系统未回退到其他版本")
    return p


def _current_version_share(token: str, conn: Connection):
    """文档级分享链接：始终解析为该文档最近一次成功发布的版本。

    重发只改变 documents.current_version 的指向，链接 token 本身不变；
    因此已发出的链接内容会跟随最新成功发布版。
    """
    if not token or len(token) > 128:
        raise HTTPException(status_code=404, detail="分享链接无效")
    doc = db.get_document_by_share(conn, token)
    if not doc or doc["status"] != "published" or not doc["current_version"]:
        raise HTTPException(status_code=404, detail="分享链接无效或文档未发布")
    version = db.get_version(conn, doc["id"], doc["current_version"])
    if not version:
        raise HTTPException(status_code=500, detail="当前版本记录缺失，系统未回退到其他版本")
    return doc, version


def _pinned_version_share(token: str, conn: Connection):
    """版本级分享链接：永久固定在创建时指定的版本，后续重发不改变其内容。"""
    if not token or len(token) > 128:
        raise HTTPException(status_code=404, detail="分享链接无效")
    share = db.get_version_share_by_token(conn, token)
    if not share:
        raise HTTPException(status_code=404, detail="分享链接无效")
    version = db.get_version(conn, share["document_id"], share["version"])
    if not version:
        raise HTTPException(status_code=404, detail="分享链接无效")
    doc = db.get_document(conn, share["document_id"])
    if not doc:
        raise HTTPException(status_code=404, detail="分享链接无效")
    return doc, version


@router.get("/{token}/info")
def share_info(token: str, conn: Connection = Depends(get_conn)):
    doc, version = _current_version_share(token, conn)
    return {
        "filename": doc["filename"],
        "published_at": version["published_at"],
        "version": version["version"],
    }


@router.get("/{token}")
def share_download(token: str, conn: Connection = Depends(get_conn)):
    """文档级链接：无需登录，只可下载当前已发布的脱敏版（原件不暴露）。"""
    doc, version = _current_version_share(token, conn)
    path = _require_file(version["file_path"])
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"redacted-{doc['filename']}",
    )


@router.get("/v/{token}/info")
def share_version_info(token: str, conn: Connection = Depends(get_conn)):
    doc, version = _pinned_version_share(token, conn)
    return {
        "filename": doc["filename"],
        "published_at": version["published_at"],
        "version": version["version"],
    }


@router.get("/v/{token}")
def share_version_download(token: str, conn: Connection = Depends(get_conn)):
    """版本级链接：持链者只能下载该链接固定版本的脱敏成品。"""
    doc, version = _pinned_version_share(token, conn)
    path = _require_file(version["file_path"])
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"redacted-v{version['version']}-{doc['filename']}",
    )
