"""命令行播种：python -m app.seed_cli"""
import asyncio

from .db import Base, SessionLocal, engine
from .seed_data import seed_all


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        summary = await seed_all(db, "admin")
    print("播种完成：", summary)


if __name__ == "__main__":
    asyncio.run(main())
