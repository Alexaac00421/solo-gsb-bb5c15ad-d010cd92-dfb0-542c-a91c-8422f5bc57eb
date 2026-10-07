from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlite3 import Connection

from .. import config, db, security
from ..deps import get_conn, current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        config.SESSION_COOKIE,
        token,
        max_age=config.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
    )


@router.post("/register")
def register(body: Credentials, response: Response, conn: Connection = Depends(get_conn)):
    if not config.ALLOW_REGISTRATION and db.count_users(conn) > 0:
        raise HTTPException(status_code=403, detail="当前未开放注册")
    username = body.username.strip()
    if db.user_by_name(conn, username):
        raise HTTPException(status_code=409, detail="用户名已被占用")
    user_id = db.create_user(conn, username, security.hash_password(body.password))
    token = security.new_token()
    db.create_session(conn, token, user_id, config.SESSION_TTL_HOURS)
    _set_session_cookie(response, token)
    return {"id": user_id, "username": username}


@router.post("/login")
def login(body: LoginBody, response: Response, conn: Connection = Depends(get_conn)):
    user = db.user_by_name(conn, body.username.strip())
    if not user or not security.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = security.new_token()
    db.create_session(conn, token, user["id"], config.SESSION_TTL_HOURS)
    _set_session_cookie(response, token)
    return {"id": user["id"], "username": user["username"]}


@router.post("/logout")
def logout(request: Request, response: Response, conn: Connection = Depends(get_conn)):
    token = request.cookies.get(config.SESSION_COOKIE)
    if token:
        db.delete_session(conn, token)
    response.delete_cookie(config.SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(user=Depends(current_user)):
    return {"id": user["id"], "username": user["username"]}
