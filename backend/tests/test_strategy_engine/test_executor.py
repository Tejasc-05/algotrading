import numpy as np
import pandas as pd
import pytest

from app.schemas.strategy import StrategyGraph
from app.strategy_engine.executor import StrategyExecutor


def _node(node_id: str, node_type: str, params: dict | None = None) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"label": node_type, "nodeType": node_type, "params": params or {}},
    }


def _edge(edge_id: str, source: str, target: str) -> dict:
    return {"id": edge_id, "source": source, "target": target}


def make_dummy_candles(n_bars: int = 50, trend: str = "down") -> pd.DataFrame:
    """Generate dummy OHLCV data."""
    dates = pd.date_range("2026-01-01", periods=n_bars, freq="1h")
    if trend == "down":
        # Strongly falling prices so RSI < 30
        close = np.linspace(100, 50, n_bars)
    elif trend == "up":
        # Strongly rising prices so RSI > 70
        close = np.linspace(50, 100, n_bars)
    elif trend == "cross_above":
        # First 25 bars flat low, then jump high
        close = np.concatenate([np.linspace(50, 50, 25), np.linspace(60, 90, 25)])
    else:
        close = np.sin(np.linspace(0, 10, n_bars)) * 10 + 50

    return pd.DataFrame({
        "timestamp": dates,
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": np.full(n_bars, 1000.0),
    })


def test_rsi_oversold_triggers_buy():
    """RSI < 30 -> BUY should emit BUY signal when market has falling prices."""
    graph = StrategyGraph.model_validate({
        "nodes": [
            _node("1", "rsi", {"period": 14}),
            _node("2", "condition", {"operator": "<", "value": 30}),
            _node("3", "buy", {"amount": 0.5}),
        ],
        "edges": [
            _edge("e1", "1", "2"),
            _edge("e2", "2", "3"),
        ],
    })

    executor = StrategyExecutor(graph)
    candles = make_dummy_candles(50, trend="down")
    signals = executor.evaluate({
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candles": candles,
        "current_price": float(candles.iloc[-1]["close"]),
        "sentiment": None,
    })

    assert len(signals) == 1
    assert signals[0].action == "buy"
    assert signals[0].params.get("amount") == 0.5


def test_rsi_overbought_triggers_sell():
    """RSI > 70 -> SELL should emit SELL signal when market has rising prices."""
    graph = StrategyGraph.model_validate({
        "nodes": [
            _node("1", "rsi", {"period": 14}),
            _node("2", "condition", {"operator": ">", "value": 70}),
            _node("3", "sell", {"amount": 0.2}),
        ],
        "edges": [
            _edge("e1", "1", "2"),
            _edge("e2", "2", "3"),
        ],
    })

    executor = StrategyExecutor(graph)
    candles = make_dummy_candles(50, trend="up")
    signals = executor.evaluate({
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candles": candles,
        "current_price": float(candles.iloc[-1]["close"]),
        "sentiment": None,
    })

    assert len(signals) == 1
    assert signals[0].action == "sell"
    assert signals[0].params.get("amount") == 0.2


def test_and_logic_both_true():
    """(RSI < 50 AND Price < 60) -> BUY should trigger when both true."""
    graph = StrategyGraph.model_validate({
        "nodes": [
            _node("n_rsi", "rsi", {"period": 14}),
            _node("c_rsi", "condition", {"operator": "<", "value": 50}),
            _node("n_price", "price", {}),
            _node("c_price", "condition", {"operator": "<", "value": 60}),
            _node("n_and", "and", {}),
            _node("n_buy", "buy", {"amount": 1.0}),
        ],
        "edges": [
            _edge("e1", "n_rsi", "c_rsi"),
            _edge("e2", "n_price", "c_price"),
            _edge("e3", "c_rsi", "n_and"),
            _edge("e4", "c_price", "n_and"),
            _edge("e5", "n_and", "n_buy"),
        ],
    })

    executor = StrategyExecutor(graph)
    candles = make_dummy_candles(50, trend="down")  # Final price is 50, RSI < 30
    signals = executor.evaluate({
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candles": candles,
        "current_price": float(candles.iloc[-1]["close"]),
        "sentiment": None,
    })

    assert len(signals) == 1
    assert signals[0].action == "buy"


def test_and_logic_one_false_emits_nothing():
    """(RSI < 30 AND Price > 80) -> BUY should NOT trigger when price is 50."""
    graph = StrategyGraph.model_validate({
        "nodes": [
            _node("n_rsi", "rsi", {"period": 14}),
            _node("c_rsi", "condition", {"operator": "<", "value": 30}),
            _node("n_price", "price", {}),
            _node("c_price", "condition", {"operator": ">", "value": 80}),
            _node("n_and", "and", {}),
            _node("n_buy", "buy", {"amount": 1.0}),
        ],
        "edges": [
            _edge("e1", "n_rsi", "c_rsi"),
            _edge("e2", "n_price", "c_price"),
            _edge("e3", "c_rsi", "n_and"),
            _edge("e4", "c_price", "n_and"),
            _edge("e5", "n_and", "n_buy"),
        ],
    })

    executor = StrategyExecutor(graph)
    candles = make_dummy_candles(50, trend="down")
    signals = executor.evaluate({
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candles": candles,
        "current_price": float(candles.iloc[-1]["close"]),
        "sentiment": None,
    })

    assert len(signals) == 0


def test_or_logic_one_true_triggers():
    """(RSI < 30 OR Price > 1000) -> BUY triggers when RSI is oversold."""
    graph = StrategyGraph.model_validate({
        "nodes": [
            _node("n_rsi", "rsi", {"period": 14}),
            _node("c_rsi", "condition", {"operator": "<", "value": 30}),
            _node("n_price", "price", {}),
            _node("c_price", "condition", {"operator": ">", "value": 1000}),
            _node("n_or", "or", {}),
            _node("n_buy", "buy", {"amount": 1.0}),
        ],
        "edges": [
            _edge("e1", "n_rsi", "c_rsi"),
            _edge("e2", "n_price", "c_price"),
            _edge("e3", "c_rsi", "n_or"),
            _edge("e4", "c_price", "n_or"),
            _edge("e5", "n_or", "n_buy"),
        ],
    })

    executor = StrategyExecutor(graph)
    candles = make_dummy_candles(50, trend="down")
    signals = executor.evaluate({
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candles": candles,
        "current_price": float(candles.iloc[-1]["close"]),
        "sentiment": None,
    })

    assert len(signals) == 1
    assert signals[0].action == "buy"


def test_risk_stop_loss_signal():
    """BUY node connected to stopLoss risk node emits both signals."""
    graph = StrategyGraph.model_validate({
        "nodes": [
            _node("1", "rsi", {"period": 14}),
            _node("2", "condition", {"operator": "<", "value": 30}),
            _node("3", "buy", {"amount": 0.5}),
            _node("4", "stopLoss", {"percent": 2}),
        ],
        "edges": [
            _edge("e1", "1", "2"),
            _edge("e2", "2", "3"),
            _edge("e3", "2", "4"),
        ],
    })

    executor = StrategyExecutor(graph)
    candles = make_dummy_candles(50, trend="down")
    signals = executor.evaluate({
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candles": candles,
        "current_price": float(candles.iloc[-1]["close"]),
        "sentiment": None,
    })

    actions = [s.action for s in signals]
    assert "buy" in actions
    assert "set_stop_loss" in actions
