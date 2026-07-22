"""
Publishes an Event to both Redis (for future live consumption — dashboard,
log tailing) and Postgres (the permanent system of record).
"""

from __future__ import annotations

from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.orchestrator.config import settings
from src.orchestrator.core.events import Event
from src.orchestrator.persistence.models import EventRow

logger = structlog.get_logger(__name__)

_redis_client: Redis | None = None


def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        # pyright: ignore[reportUnknownMemberType]
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def publish_event(event: Event, session: AsyncSession) -> None:
    """
    Persist the event to Postgres (source of truth), then best-effort
    publish to Redis (live tap for future consumers).
    """
    row = EventRow(
        id=event.event_id,
        run_id=event.run_id,
        node_id=event.node_id,
        agent_name=event.agent_name,
        event_type=event.event_type.value,
        payload=event.payload,
    )
    session.add(row)
    await session.commit()

    try:
        redis = get_redis()
        stream_key = f"run:{event.run_id}:events"
        # Cast fields to bypass dict invariance checks and capture the returned entry ID
        fields: dict[Any, Any] = event.to_redis_dict()
        _ = await redis.xadd(stream_key, fields)
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis_publish_failed", run_id=event.run_id, error=str(exc))
