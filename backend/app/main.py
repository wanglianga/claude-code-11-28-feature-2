"""FastAPI 入口。

启动时幂等建表；不提供 Swagger UI 之外的额外前端托管（前端为独立 React 工程）。
环境变量 AUTO_SEED=1 时在容器首次启动幂等播种虚构样本，便于一键演示。
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import Base, engine
from .routers import admin, analysis, auth, review, rosters, subjects, finance

# 确保模型被导入以便 create_all 注册
from . import models  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if os.getenv("AUTO_SEED") == "1":
        from .db import SessionLocal
        from .seed_data import seed_all
        async with SessionLocal() as session:
            await seed_all(session, "admin")
    yield


app = FastAPI(
    title="企业股权关系分析平台 API",
    version="1.0.0",
    description="虚构样本的股权经济权益与表决控制分析，不连接工商网站，不作真实投资或法律结论。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(subjects.router)
app.include_router(rosters.router)
app.include_router(review.router)
app.include_router(analysis.router)
app.include_router(finance.router)
app.include_router(admin.router)


@app.get("/api/health", tags=["meta"])
async def health():
    return {"status": "ok", "service": "equity-analysis"}
