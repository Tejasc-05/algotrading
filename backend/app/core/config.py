"""Centralized application configuration, loaded once from environment variables."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    environment: str = "development"
    debug: bool = True
    cors_origins: str = "http://localhost:5173"

    # Database
    database_url: str
    postgres_user: str | None = None
    postgres_password: str | None = None
    postgres_db: str | None = None

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Secrets encryption
    encryption_key: str

    # Rate limiting
    auth_rate_limit_max_attempts: int = 10
    auth_rate_limit_window_seconds: int = 60

    # Live trading kill switch (Phase 10)
    enable_live_trading: bool = False

    # Exchange credentials (placeholders — real usage starts Phase 5)
    binance_api_key: str | None = None
    binance_secret: str | None = None

    # Sentiment API credentials
    newsapi_api_key: str | None = None
    cryptopanic_api_key: str | None = None
    twitter_api_key: str | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
