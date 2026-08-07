"""Small PostgreSQL transaction locks for cross-process lifecycle boundaries."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ALLOCATION_BOUNDARY_LOCK_ID = 1_294_671_824


async def lock_allocation_boundary(session: AsyncSession) -> None:
    """Serialize snapshot starts with jobs that change global sample allocation."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _ALLOCATION_BOUNDARY_LOCK_ID},
    )
