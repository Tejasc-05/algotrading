"""Concrete SentimentProvider implementations.

Supports NewsAPI, CryptoPanic, and Twitter/X as backends.
Each provider fetches crypto-related content, scores sentiment,
and returns a normalized SentimentResult.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.sentiment.service import SentimentProvider, SentimentResult


@dataclass
class ProviderConfig:
    api_key: str
    source_name: str


class NewsAPISentimentProvider(SentimentProvider):
    """Fetches crypto news from NewsAPI and scores sentiment."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or get_settings().newsapi_api_key
        self._base_url = "https://newsapi.org/v2/everything"

    async def get_sentiment(self, symbol: str) -> SentimentResult:
        """Fetch news for a symbol and compute sentiment score."""
        # In production, this would make actual HTTP calls to NewsAPI
        # For now, return a computed score based on keyword matching
        # Placeholder implementation — replace with actual API call:
        # async with httpx.AsyncClient() as client:
        #     response = await client.get(
        #         self._base_url,
        #         params={"q": symbol, "apiKey": self._api_key, "language": "en"},
        #     )
        #     articles = response.json().get("articles", [])
        #     score = self._compute_sentiment_from_articles(articles)

        # Placeholder: return a simulated score based on symbol
        score = self._simulate_sentiment(symbol)
        label = "positive" if score > 0.1 else ("negative" if score < -0.1 else "neutral")

        return SentimentResult(
            symbol=symbol,
            score=score,
            label=label,
            confidence=abs(score),
            as_of=datetime.now(timezone.utc),
        )

    def _simulate_sentiment(self, symbol: str) -> float:
        """Simulate sentiment score (placeholder for real API)."""
        import hashlib
        hash_val = int(hashlib.md5(symbol.encode()).hexdigest(), 16)
        return ((hash_val % 200) - 100) / 100.0


class CryptoPanicSentimentProvider(SentimentProvider):
    """Fetches crypto sentiment from CryptoPanic."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or get_settings().cryptopanic_api_key
        self._base_url = "https://cryptopanic.com/api/v1/posts/"

    async def get_sentiment(self, symbol: str) -> SentimentResult:
        """Fetch posts for a symbol and compute sentiment score."""
        # Placeholder for actual CryptoPanic API integration
        # async with httpx.AsyncClient() as client:
        #     response = await client.get(
        #         f"{self._base_url}{symbol}/",
        #         params={"auth_token": self._api_key, "public": True},
        #     )
        #     posts = response.json().get("results", [])
        #     score = self._compute_sentiment_from_posts(posts)

        score = self._simulate_sentiment(symbol)
        label = "positive" if score > 0.1 else ("negative" if score < -0.1 else "neutral")

        return SentimentResult(
            symbol=symbol,
            score=score,
            label=label,
            confidence=abs(score),
            as_of=datetime.now(timezone.utc),
        )

    def _simulate_sentiment(self, symbol: str) -> float:
        import hashlib
        hash_val = int(hashlib.md5(symbol.encode()).hexdigest(), 16)
        return ((hash_val % 200) - 100) / 100.0


class TwitterSentimentProvider(SentimentProvider):
    """Fetches tweets for a symbol and scores sentiment."""

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or get_settings().twitter_api_key
        # Uses Twitter API v2
        self._base_url = "https://api.twitter.com/2/tweets/search/recent"

    async def get_sentiment(self, symbol: str) -> SentimentResult:
        """Fetch tweets and compute sentiment score."""
        # Placeholder for actual Twitter API v2 integration
        # async with httpx.AsyncClient() as client:
        #     response = await client.get(
        #         self._base_url,
        #         headers={"Authorization": f"Bearer {self._api_key}"},
        #         params={"query": f"{symbol} lang:en", "max_results": 100},
        #     )
        #     tweets = response.json().get("data", [])
        #     score = self._compute_sentiment_from_tweets(tweets)

        score = self._simulate_sentiment(symbol)
        label = "positive" if score > 0.1 else ("negative" if score < -0.1 else "neutral")

        return SentimentResult(
            symbol=symbol,
            score=score,
            label=label,
            confidence=abs(score),
            as_of=datetime.now(timezone.utc),
        )

    def _simulate_sentiment(self, symbol: str) -> float:
        import hashlib
        hash_val = int(hashlib.md5(symbol.encode()).hexdigest(), 16)
        return ((hash_val % 200) - 100) / 100.0


# Provider registry for easy lookup
PROVIDER_REGISTRY: dict[str, type[SentimentProvider]] = {
    "newsapi": NewsAPISentimentProvider,
    "cryptopanic": CryptoPanicSentimentProvider,
    "twitter": TwitterSentimentProvider,
}


def get_sentiment_provider(provider_name: str, api_key: str | None = None) -> SentimentProvider:
    """Get a sentiment provider by name."""
    provider_cls = PROVIDER_REGISTRY.get(provider_name.lower())
    if provider_cls is None:
        raise AppError(
            code="UNKNOWN_PROVIDER",
            message=f"Unknown sentiment provider: {provider_name}",
            status_code=400,
        )
    return provider_cls(api_key=api_key)