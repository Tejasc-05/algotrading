"""Exchange connection endpoints. Credentials are always encrypted at rest
(see app.core.security.encrypt_secret) and never echoed back in a response.
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_db
from app.core.config import get_settings
from app.core.exceptions import AppError, ForbiddenError
from app.core.security import decrypt_secret, encrypt_secret
from app.database.models.exchange_connection import ExchangeConnection
from app.database.models.user import User
from app.schemas.trading import ExchangeConnectRequest, ExchangeConnectionOut

router = APIRouter(prefix="/exchanges", tags=["exchanges"])


@router.post("/connect", response_model=ExchangeConnectionOut, status_code=status.HTTP_201_CREATED)
async def connect_exchange(
    payload: ExchangeConnectRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExchangeConnectionOut:
    """Connect an exchange by storing encrypted credentials."""
    settings = get_settings()

    # Validate exchange is supported by CCXT
    supported = ["binance", "binanceus", "coinbase", "coinbasepro", "kraken", "bybit", "bitfinex", "huobi", "okx"]
    if payload.exchange_name.lower() not in supported:
        raise AppError(
            code="UNSUPPORTED_EXCHANGE",
            message=f"Exchange '{payload.exchange_name}' is not supported",
            status_code=400,
        )

    # Encrypt credentials before storing
    api_key_encrypted = encrypt_secret(payload.api_key)
    api_secret_encrypted = encrypt_secret(payload.api_secret)
    passphrase_encrypted = encrypt_secret(payload.passphrase) if payload.passphrase else None

    connection = ExchangeConnection(
        user_id=current_user.id,
        exchange_name=payload.exchange_name.lower(),
        label=payload.label,
        api_key_encrypted=api_key_encrypted,
        api_secret_encrypted=api_secret_encrypted,
        passphrase_encrypted=passphrase_encrypted,
        is_testnet=payload.is_testnet,
        is_active=True,
    )

    db.add(connection)
    await db.commit()
    await db.refresh(connection)

    return ExchangeConnectionOut(
        id=connection.id,
        exchange_name=connection.exchange_name,
        label=connection.label,
        is_testnet=connection.is_testnet,
        is_active=connection.is_active,
        created_at=connection.created_at,
    )


@router.get("", response_model=list[ExchangeConnectionOut])
async def list_connections(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ExchangeConnectionOut]:
    """List all exchange connections for the current user."""
    from sqlalchemy import select
    result = await db.execute(
        select(ExchangeConnection).where(ExchangeConnection.user_id == current_user.id)
    )
    connections = result.scalars().all()
    return [
        ExchangeConnectionOut(
            id=c.id,
            exchange_name=c.exchange_name,
            label=c.label,
            is_testnet=c.is_testnet,
            is_active=c.is_active,
            created_at=c.created_at,
        )
        for c in connections
    ]


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_exchange(
    connection_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove an exchange connection."""
    from sqlalchemy import select
    result = await db.execute(
        select(ExchangeConnection).where(
            ExchangeConnection.id == connection_id,
            ExchangeConnection.user_id == current_user.id,
        )
    )
    connection = result.scalars().first()
    if connection is None:
        raise AppError(code="NOT_FOUND", message="Exchange connection not found", status_code=404)

    await db.delete(connection)
    await db.commit()


@router.post("/{connection_id}/test", response_model=dict)
async def test_connection(
    connection_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Test an exchange connection by fetching a simple ticker."""
    from sqlalchemy import select
    result = await db.execute(
        select(ExchangeConnection).where(
            ExchangeConnection.id == connection_id,
            ExchangeConnection.user_id == current_user.id,
        )
    )
    connection = result.scalars().first()
    if connection is None:
        raise AppError(code="NOT_FOUND", message="Exchange connection not found", status_code=404)

    # Decrypt credentials
    api_key = decrypt_secret(connection.api_key_encrypted)
    api_secret = decrypt_secret(connection.api_secret_encrypted)
    passphrase = (
        decrypt_secret(connection.passphrase_encrypted)
        if connection.passphrase_encrypted
        else None
    )

    # Test with CCXT
    try:
        from app.exchanges.ccxt_service import CCXTExchange
        exchange = CCXTExchange(
            exchange_id=connection.exchange_name,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            testnet=connection.is_testnet,
        )
        ticker = await exchange.fetch_ticker("BTC/USDT")
        return {"success": True, "exchange": connection.exchange_name, "ticker": ticker}
    except Exception as e:
        return {"success": False, "error": str(e)}