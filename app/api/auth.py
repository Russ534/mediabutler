"""认证：登录签发 token，FastAPI 依赖校验。"""
import secrets
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import config

router = APIRouter(prefix="/api/auth")

_tokens: dict[str, float] = {}
TOKEN_TTL = 7 * 86400


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginBody):
    if not config.check_password(body.username, body.password):
        raise HTTPException(401, "账号或密码错误")
    token = secrets.token_urlsafe(32)
    _tokens[token] = time.time() + TOKEN_TTL
    return {"token": token}


class PwdBody(BaseModel):
    old_password: str
    new_password: str


@router.post("/password")
async def change_pwd(body: PwdBody, authorization: str = Header("")):
    require(authorization)
    if not config.check_password(config.get("auth.username", "admin"), body.old_password):
        raise HTTPException(400, "原密码错误")
    import hashlib
    salt = secrets.token_hex(8)
    config.set("auth.salt", salt)
    config.set("auth.password_hash", hashlib.sha256((salt + body.new_password).encode()).hexdigest())
    return {"ok": True}


def require(authorization: str) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    exp = _tokens.get(token)
    if not exp or exp < time.time():
        raise HTTPException(401, "未登录或登录已过期")
