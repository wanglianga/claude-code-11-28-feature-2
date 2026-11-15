"""极简 JWT 认证与演示账号。演示用途，密码以哈希形式内置，不接外部身份源。"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login-form", auto_error=False)

# 演示账号（README 中公开）：analyst 分析员 / reviewer 审核员 / admin 两者皆可
DEMO_USERS = {
    "analyst": {
        "password_sha256": hashlib.sha256(b"analyst123").hexdigest(),
        "role": "analyst",
    },
    "reviewer": {
        "password_sha256": hashlib.sha256(b"reviewer123").hexdigest(),
        "role": "reviewer",
    },
    "admin": {
        "password_sha256": hashlib.sha256(b"admin123").hexdigest(),
        "role": "admin",
    },
}


def verify_password(password: str, stored_sha256: str) -> bool:
    return hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), stored_sha256)


def create_token(username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def current_user(token: str | None = Depends(oauth2_scheme)) -> dict:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供令牌")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期")
    username = payload.get("sub")
    if username not in DEMO_USERS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return {"username": username, "role": DEMO_USERS[username]["role"]}


def require_roles(*roles: str):
    async def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] != "admin" and user["role"] not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前角色无权执行该操作")
        return user
    return checker


# 便于其他模块复用会话依赖
session_dep = Depends(get_db)
