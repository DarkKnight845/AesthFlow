"""
Async SQLAlchemy engine + session factory
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

<<<<<<< HEAD
from orchestrator.config import settings
=======
from src.orchestrator.config import settings
>>>>>>> f80551f (AesthFlow version1.0)

engine = create_async_engine(settings.database_url, echo=(settings.environment == "development"))

AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session