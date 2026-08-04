from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def raw_psycopg_connection(session: AsyncSession):
    """Unwrap the driver connection so we can use psycopg COPY for bulk inserts.

    Bulk sample ingestion is the only hot path in this system (100M rows),
    ORM inserts are far too slow there.
    """
    conn = await session.connection()
    raw = await conn.get_raw_connection()
    return raw.driver_connection
