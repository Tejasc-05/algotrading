"""Sentiment endpoint using pluggable providers (NewsAPI, CryptoPanic, Twitter/X)."""
from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_user
from app.core.config import get_settings
from app.database.models.user import User
from app.schemas.sentiment import SentimentOut
from app.sentiment.providers import get_sentiment_provider
from app.sentiment.service import SentimentService

router = APIRouter(prefix="/sentiment", tags=["sentiment"])


@router.get("/{symbol}", response_model=SentimentOut)
async def get_sentiment(
    symbol: str,
    provider: str = Query("newsapi", description="Sentiment provider: newsapi, cryptopanic, twitter"),
    current_user: User = Depends(get_current_user),
) -> SentimentOut:
    """Get sentiment for a symbol using the specified provider."""
    settings = get_settings()
    provider_instance = get_sentiment_provider(provider)
    service = SentimentService(provider_instance)
    result = await service.get_sentiment(symbol)
    return SentimentOut(
        symbol=result.symbol,
        score=result.score,
        label=result.label,
        confidence=result.confidence,
        as_of=result.as_of,
    )
