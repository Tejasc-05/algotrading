"""Shared FastAPI dependencies: DB/Redis session injection, the current-user
resolver, and a small Redis-backed rate limiter used on the auth routes.
"""
import redis.asyncio as redis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import RateLimitedError, UnauthorizedError
from app.core.security import TokenError, decode_access_token
from app.database.database import get_db
from app.database.models.user import User
from app.database.repositories.user_repository import UserRepository
from app.redis.cache import increment_with_expiry
from app.redis.client import get_redis

__all__ = ["get_db", "get_redis", "get_current_user", "RateLimiter"]

settings = get_settings()
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise UnauthorizedError("Missing bearer token")

    try:
        payload = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise UnauthorizedError(str(exc)) from exc

    user_id = payload.get("sub")
    user = await UserRepository(db).get(user_id) if user_id else None
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive")
    return user


async def get_user_from_websocket(websocket: Request | any) -> User | None:
    token = websocket.query_params.get("token")
    if not token:
        auth = websocket.headers.get("authorization")
        if auth and auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1]
    if not token:
        protocols = websocket.headers.get("sec-websocket-protocol", "").split(",")
        for p in protocols:
            p = p.strip()
            if len(p) > 20:
                token = p
                break
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except TokenError:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    from app.database.database import async_session_factory
    async with async_session_factory() as db:
        user = await UserRepository(db).get(user_id)
        if user and user.is_active:
            return user
    return None


class RateLimiter:
    """Redis fixed-window limiter. Usage:
    `Depends(RateLimiter("login"))` on a route.
    """

    def __init__(self, key_prefix: str, max_attempts: int | None = None, window_seconds: int | None = None):
        self.key_prefix = key_prefix
        self.max_attempts = max_attempts or settings.auth_rate_limit_max_attempts
        self.window_seconds = window_seconds or settings.auth_rate_limit_window_seconds

    async def __call__(self, request: Request, redis_client: redis.Redis = Depends(get_redis)) -> None:
        client_host = request.client.host if request.client else "unknown"
        key = f"ratelimit:{self.key_prefix}:{client_host}"
        count = await increment_with_expiry(redis_client, key, self.window_seconds)
        if count > self.max_attempts:
            raise RateLimitedError(f"Too many {self.key_prefix} attempts — please try again later")
