from datetime import datetime, timezone
import pytest
from httpx import AsyncClient

from tests.test_strategy_engine.test_validator import _node, _edge


async def _create_test_strategy(client: AsyncClient, headers: dict[str, str]) -> str:
    """Helper to create a strategy and return its ID."""
    payload = {
        "name": "Test Momentum Strategy",
        "description": "RSI momentum strategy for testing",
        "graph": {
            "nodes": [
                _node("rsi-1", "rsi", {"period": 14}),
                _node("cond-1", "condition", {"operator": "<", "value": 30}),
                _node("buy-1", "buy", {"amount": 0.1, "type": "market"}),
                _node("sl-1", "stopLoss", {"percent": 2}),
            ],
            "edges": [
                _edge("e1", "rsi-1", "cond-1"),
                _edge("e2", "cond-1", "buy-1"),
                _edge("e3", "cond-1", "sl-1"),
            ],
        },
    }
    res = await client.post("/api/strategies", json=payload, headers=headers)
    assert res.status_code == 201
    return res.json()["id"]


@pytest.mark.asyncio
async def test_market_endpoints(client: AsyncClient, auth_headers: dict[str, str]):
    # Ticker
    res = await client.get("/api/market/ticker?symbol=BTC/USDT", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["symbol"] == "BTC/USDT"
    assert "price" in data
    assert data["price"] > 0

    # OHLCV
    res2 = await client.get("/api/market/ohlcv?symbol=BTC/USDT&limit=10", headers=auth_headers)
    assert res2.status_code == 200
    candles = res2.json()
    assert isinstance(candles, list)
    assert len(candles) > 0
    assert "close" in candles[0]


@pytest.mark.asyncio
async def test_backtest_endpoints(client: AsyncClient, auth_headers: dict[str, str]):
    strategy_id = await _create_test_strategy(client, auth_headers)

    # Run backtest
    payload = {
        "strategy_id": strategy_id,
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "start_date": "2026-01-01T00:00:00Z",
        "end_date": "2026-01-05T00:00:00Z",
        "starting_capital": 10000.0,
        "fees_pct": 0.1,
        "slippage_pct": 0.05,
        "engine": "vectorbt",
    }
    res = await client.post("/api/backtest", json=payload, headers=auth_headers)
    assert res.status_code == 202
    data = res.json()
    bt_id = data["id"]
    assert bt_id.startswith("bt_") or len(bt_id) > 10
    assert "metrics" in data
    assert "equity_curve" in data

    # Retrieve backtest by ID
    res2 = await client.get(f"/api/backtest/{bt_id}", headers=auth_headers)
    assert res2.status_code == 200
    retrieved = res2.json()
    assert retrieved["id"] == bt_id
    assert retrieved["metrics"]["final_portfolio_value"] > 0


@pytest.mark.asyncio
async def test_paper_trading_endpoints(client: AsyncClient, auth_headers: dict[str, str]):
    strategy_id = await _create_test_strategy(client, auth_headers)

    # Start paper trading
    start_payload = {
        "strategy_id": strategy_id,
        "symbol": "BTC/USDT",
        "starting_balance": 15000.0,
    }
    start_res = await client.post("/api/paper-trading/start", json=start_payload, headers=auth_headers)
    assert start_res.status_code == 201
    account_data = start_res.json()
    account_id = account_data["id"]
    assert account_data["balance"] == 15000.0
    assert account_data["is_active"] is True

    # Get active paper account
    acc_res = await client.get("/api/paper-trading/account", headers=auth_headers)
    assert acc_res.status_code == 200
    assert acc_res.json()["id"] == account_id

    # Create paper order
    order_payload = {
        "paper_account_id": account_id,
        "symbol": "BTC/USDT",
        "side": "buy",
        "order_type": "market",
        "quantity": 0.1,
    }
    order_res = await client.post("/api/paper-trading/orders", json=order_payload, headers=auth_headers)
    assert order_res.status_code == 201
    order_id = order_res.json()["order_id"]

    # Simulate fill
    fill_payload = {
        "order_id": order_id,
        "current_price": 50000.0,
    }
    fill_res = await client.post("/api/paper-trading/simulate-fill", json=fill_payload, headers=auth_headers)
    assert fill_res.status_code == 200
    assert fill_res.json()["executed_price"] > 0

    # List paper trades
    trades_res = await client.get("/api/paper-trading/trades", headers=auth_headers)
    assert trades_res.status_code == 200
    trades = trades_res.json()
    assert len(trades) >= 1
    assert trades[0]["symbol"] == "BTC/USDT"

    # Run tick
    tick_res = await client.post("/api/paper-trading/tick", json={"paper_account_id": account_id}, headers=auth_headers)
    assert tick_res.status_code == 200

    # Stop paper trading
    stop_res = await client.post("/api/paper-trading/stop", json={"paper_account_id": account_id}, headers=auth_headers)
    assert stop_res.status_code == 200
    assert stop_res.json()["is_active"] is False


@pytest.mark.asyncio
async def test_portfolio_endpoints(client: AsyncClient, auth_headers: dict[str, str]):
    res = await client.get("/api/portfolio", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "total_value" in data
    assert "positions" in data

    res2 = await client.get("/api/portfolio/performance", headers=auth_headers)
    assert res2.status_code == 200
    perf = res2.json()
    assert "equity_curve" in perf


@pytest.mark.asyncio
async def test_sentiment_endpoints(client: AsyncClient, auth_headers: dict[str, str]):
    res = await client.get("/api/sentiment/BTC", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["symbol"] == "BTC"
    assert -1.0 <= data["score"] <= 1.0
    assert data["label"] in ("positive", "negative", "neutral")


@pytest.mark.asyncio
async def test_exchange_connections(client: AsyncClient, auth_headers: dict[str, str]):
    # Connect exchange
    connect_payload = {
        "exchange_name": "binance",
        "label": "My Test Binance",
        "api_key": "dummy-api-key",
        "api_secret": "dummy-api-secret",
        "is_testnet": True,
    }
    res = await client.post("/api/exchanges/connect", json=connect_payload, headers=auth_headers)
    assert res.status_code == 201
    conn_data = res.json()
    conn_id = conn_data["id"]
    assert conn_data["exchange_name"] == "binance"
    # Secrets must NEVER be leaked in responses
    assert "api_secret" not in conn_data
    assert "dummy-api-secret" not in str(conn_data)

    # List exchanges
    list_res = await client.get("/api/exchanges", headers=auth_headers)
    assert list_res.status_code == 200
    assert any(c["id"] == conn_id for c in list_res.json())

    # Disconnect exchange
    del_res = await client.delete(f"/api/exchanges/{conn_id}", headers=auth_headers)
    assert del_res.status_code == 204
