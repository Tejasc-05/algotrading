"""WebSocket connection registry + broadcast, fed by app.redis.pubsub so the
frontend receives real-time updates without polling.

Architecture:
    Exchange / Market Data -> Redis (pub/sub) -> WebSocketManager -> React frontend

Event names: price_update, candle_update, strategy_signal,
paper_order, portfolio_update, pnl_update, sentiment_update, bot_status.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from app.core.logging import get_logger

logger = get_logger(__name__)


class WebSocketManager:
    """Tracks active WebSocket connections per user and broadcasts events."""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection for a user."""
        await websocket.accept()
        if user_id not in self._connections:
            self._connections[user_id] = []
        self._connections[user_id].append(websocket)
        logger.info(f"WebSocket connected for user {user_id}")

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        """Remove a disconnected WebSocket from a user's connection list."""
        if user_id in self._connections:
            try:
                self._connections[user_id].remove(websocket)
            except ValueError:
                pass
            if not self._connections[user_id]:
                del self._connections[user_id]
        logger.info(f"WebSocket disconnected for user {user_id}")

    async def broadcast(self, user_id: str, event: str, payload: dict) -> None:
        """Broadcast an event to all connections for a specific user."""
        if user_id not in self._connections:
            return

        message = json.dumps({"event": event, "data": payload})

        # Send to all connections for this user, removing dead ones
        dead: list[WebSocket] = []
        for ws in self._connections[user_id]:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._connections[user_id].remove(ws)

        logger.debug(f"Broadcast '{event}' to user {user_id}")

    async def broadcast_to_all(self, event: str, payload: dict) -> None:
        """Broadcast to all connected users."""
        for user_id in list(self._connections.keys()):
            await self.broadcast(user_id, event, payload)

    async def send_to_user(self, user_id: str, event: str, payload: dict) -> None:
        """Send a single event to all of a user's connections."""
        await self.broadcast(user_id, event, payload)


manager = WebSocketManager()


async def websocket_endpoint(webSocket: WebSocket) -> None:
    """WebSocket endpoint — accepts a token param, registers the connection,
    subscribes to Redis pub/sub for real-time updates, and relays events
    to the client until the connection is closed."""
    from app.api.dependencies import get_user_from_websocket

    user = await get_user_from_websocket(webSocket)
    if user is None:
        await webSocket.close(code=1008)
        return

    await manager.connect(user.id, webSocket)

    try:
        # Subscribe to Redis pub/sub for this user's events
        from app.redis.pubsub import subscribe_to_user_channel

        redis_messages = subscribe_to_user_channel(user.id)
        redis_task = asyncio.create_task(
            _relay_redis_to_websocket(user.id, webSocket, redis_messages)
        )

        # Keep connection alive
        while True:
            await webSocket.receive_text()

    except WebSocketDisconnect:
        manager.disconnect(user.id, webSocket)
    except Exception as e:
        logger.error(f"WebSocket error for user {user.id}: {e}")
        manager.disconnect(user.id, webSocket)
    finally:
        if "redis_task" in locals():
            redis_task.cancel()


async def _relay_redis_to_websocket(user_id: str, websocket: WebSocket, redis_messages) -> None:
    """Relay Redis pub/sub messages to the WebSocket client."""
    async for message in redis_messages:
        try:
            await websocket.send_text(message)
        except Exception:
            break