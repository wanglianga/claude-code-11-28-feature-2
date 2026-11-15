"""演示管理：一键播种虚构样本。仅用于隔离演示环境。"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_roles
from ..db import get_db
from ..seed_data import seed_all

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/seed")
async def seed_demo(db: AsyncSession = Depends(get_db),
                    user: dict = Depends(require_roles("admin"))):
    return await seed_all(db, user["username"])
