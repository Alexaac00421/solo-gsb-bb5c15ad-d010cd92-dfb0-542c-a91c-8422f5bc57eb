"""认证与授权依赖。"""
from fastapi import Depends, HTTPException, Request
from sqlite3 import Connection

from . import config, db


def get_conn():
    yield from db.get_db()


def current_user(request: Request, conn: Connection = Depends(get_conn)):
    token = request.cookies.get(config.SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    user = db.session_user(conn, token)
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return user


def owned_document(doc_id: str, conn: Connection = Depends(get_conn), user=Depends(current_user)):
    """仅文档创建者可访问。"""
    doc = db.get_document(conn, doc_id)
    if not doc or doc["owner_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc
