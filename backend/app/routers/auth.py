"""认证路由。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from ..auth import DEMO_USERS, create_token, current_user, verify_password
from ..schemas import LoginIn, TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _issue(username: str, password: str) -> TokenOut:
    user = DEMO_USERS.get(username)
    if not user or not verify_password(password, user["password_sha256"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return TokenOut(access_token=create_token(username, user["role"]), role=user["role"])


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn):
    return _issue(body.username, body.password)


@router.post("/login-form", response_model=TokenOut, include_in_schema=False)
async def login_form(form: OAuth2PasswordRequestForm = Depends()):
    return _issue(form.username, form.password)


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    return user
