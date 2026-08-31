from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


def _engine_connect_args() -> dict:
    url = settings.async_database_url.lower()
    if url.startswith("postgresql") and "sslmode=require" in url:
        return {"ssl": "require"}
    return {}


engine = create_async_engine(
    settings.async_database_url,
    echo=settings.environment == "development",
    connect_args=_engine_connect_args(),
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    import app.models  # noqa: F401 — register all ORM tables on Base.metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
