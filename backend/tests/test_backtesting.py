from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from app.backtesting.backtrader_engine import run_backtrader_backtest
from app.backtesting.metrics import compute_metrics
from app.backtesting.vectorbt_engine import run_vectorbt_backtest
from app.schemas.strategy import StrategyGraph


def _node(node_id: str, node_type: str, params: dict | None = None) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"label": node_type, "nodeType": node_type, "params": params or {}},
    }


def _edge(edge_id: str, source: str, target: str) -> dict:
    return {"id": edge_id, "source": source, "target": target}


def test_compute_metrics_accuracy():
    equity_curve = [
        {"timestamp": datetime(2026, 1, 1), "value": 10000.0},
        {"timestamp": datetime(2026, 1, 2), "value": 10500.0},
        {"timestamp": datetime(2026, 1, 3), "value": 9800.0},
        {"timestamp": datetime(2026, 1, 4), "value": 11000.0},
    ]
    trade_history = [
        {"side": "buy", "price": 100.0, "quantity": 10.0, "pnl": 500.0},
        {"side": "sell", "price": 95.0, "quantity": 10.0, "pnl": -700.0},
        {"side": "buy", "price": 110.0, "quantity": 10.0, "pnl": 1200.0},
    ]
    metrics = compute_metrics(equity_curve, trade_history, starting_capital=10000.0)

    assert metrics["final_portfolio_value"] == 11000.0
    assert metrics["net_profit"] == 1000.0
    assert metrics["total_return_pct"] == 10.0
    assert metrics["num_trades"] == 3
    # 2 wins out of 3 trades = 66.67%
    assert metrics["win_rate_pct"] == pytest.approx(66.67, abs=0.1)
    # Gross profit: 500 + 1200 = 1700. Gross loss: 700. Profit factor = 1700/700 = 2.4286
    assert metrics["profit_factor"] == pytest.approx(1700 / 700, abs=0.01)
    assert metrics["max_drawdown_pct"] > 0


@pytest.mark.asyncio
async def test_run_vectorbt_and_backtrader_backtests():
    # Strategy: RSI < 40 -> BUY, RSI > 60 -> SELL
    graph = StrategyGraph.model_validate({
        "nodes": [
            _node("n_rsi", "rsi", {"period": 14}),
            _node("c_buy", "condition", {"operator": "<", "value": 40}),
            _node("a_buy", "buy", {"amount": 0.1}),
        ],
        "edges": [
            _edge("e1", "n_rsi", "c_buy"),
            _edge("e2", "c_buy", "a_buy"),
        ],
    })

    # 100 bars oscillating price
    n_bars = 100
    dates = pd.date_range("2026-01-01", periods=n_bars, freq="1h", tz="UTC")
    prices = 100.0 + np.sin(np.linspace(0, 4 * np.pi, n_bars)) * 20.0
    candles = pd.DataFrame({
        "timestamp": dates,
        "open": prices,
        "high": prices + 1.0,
        "low": prices - 1.0,
        "close": prices,
        "volume": np.full(n_bars, 100.0),
    })

    start = dates[0].to_pydatetime()
    end = dates[-1].to_pydatetime()

    # Test VectorBT engine
    vbt_res = await run_vectorbt_backtest(
        strategy_graph=graph,
        symbol="BTC/USDT",
        timeframe="1h",
        start=start,
        end=end,
        starting_capital=10000.0,
        fees_pct=0.001,
        slippage_pct=0.0005,
        candles=candles,
    )
    assert "metrics" in vbt_res
    assert "equity_curve" in vbt_res
    assert "trade_history" in vbt_res
    assert vbt_res["metrics"]["final_portfolio_value"] > 0
    assert len(vbt_res["equity_curve"]) > 0

    # Test Backtrader engine
    bt_res = await run_backtrader_backtest(
        strategy_graph=graph,
        symbol="BTC/USDT",
        timeframe="1h",
        start=start,
        end=end,
        starting_capital=10000.0,
        fees_pct=0.001,
        slippage_pct=0.0005,
        candles=candles,
    )
    assert "metrics" in bt_res
    assert "equity_curve" in bt_res
    assert bt_res["metrics"]["final_portfolio_value"] > 0
