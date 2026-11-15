"""pytest 配置：所有测试使用独立的 ccdev_test 库，函数级重建表。"""
import os

os.environ["DATABASE_URL"] = "postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/ccdev_test"
os.environ["JWT_SECRET"] = "test-secret"

import asyncio  # noqa: E402

import pytest  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app import models  # noqa: E402,F401


@pytest.fixture()
def fresh_db():
    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(setup()) if False else asyncio.run(setup())
    yield
    async def teardown():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    asyncio.run(teardown())
