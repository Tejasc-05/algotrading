"""Redis pub/sub channels feeding the WebSocket manager (price_update,
candle_update, strategy_signal, paper_order, portfolio_update, pnl_update,
sentiment_update, bot_status). Implemented in Phase 8 — see
app.websocket.manager for the consumer side.
"""


import json
from collections.abc import AsyncGenerator

from app.core.logging import get_logger
from app.redis.client import get_redis_client

logger = get_logger(__name__)


async def publish_event(channel: str, payload: dict) -> None:
    """Publish an event to a Redis channel."""
    client = None
    try:
        client = get_redis_client()
        message = json.dumps(payload)
        await client.publish(channel, message)
    except Exception as exc:
        logger.warning("Failed to publish event to Redis channel %s: %s", channel, exc)
    finally:
        if client:
            await client.aclose()


async def publish_user_event(user_id: str, event: str, data: dict) -> None:
    """Publish a typed event to a user's channel."""
    channel = f"user:{user_id}"
    await publish_event(channel, {"event": event, "data": data})


async def subscribe(channel: str) -> AsyncGenerator[str, None]:
    """Subscribe to a Redis channel and yield message payloads."""
    client = None
    pubsub = None
    try:
        client = get_redis_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message and message.get("type") == "message":
                yield message["data"]
    except Exception as exc:
        logger.warning("Redis subscribe error on %s: %s", channel, exc)
    finally:
        try:
            if pubsub:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            if client:
                await client.aclose()
        except Exception:
            pass


async def subscribe_to_user_channel(user_id: str) -> AsyncGenerator[str, None]:
    """Subscribe to a user-specific Redis channel."""
    async for msg in subscribe(f"user:{user_id}"):
        yield msg
