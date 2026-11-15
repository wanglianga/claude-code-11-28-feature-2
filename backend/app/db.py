"""异步 SQLAlchemy 引擎与会话。"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
