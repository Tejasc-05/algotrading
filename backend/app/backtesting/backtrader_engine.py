"""Event-driven backtesting engine (Backtrader-style), used where bar-by-bar
simulation fidelity matters more than vectorized speed (e.g. strategies with
path-dependent state like trailing stops).

This is a lightweight, self-contained implementation — no Backtrader package
required. It reuses the same StrategyExecutor and simulator logic as the
vectorized engine but processes events sequentially for fidelity.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from app.backtesting.metrics import compute_metrics
from app.market_data.historical import fetch_historical_ohlcv
from app.paper_trading.simulator import DEFAULT_FEE_PCT, DEFAULT_SLIPPAGE_PCT, simulate_fill
from app.strategy_engine.executor import StrategyExecutor


async def run_backtrader_backtest(
    strategy_graph: Any,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    starting_capital: float,
    fees_pct: float,
    slippage_pct: float,
) -> dict[str, Any]:
    """Run an event-driven backtest against historical OHLCV data.

    Returns a dict matching `BacktestResultOut` schema.
    """
    # 1. Fetch historical OHLCV data
    df = await fetch_historical_ohlcv(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
    )

    if df is None or df.empty:
        raise ValueError(f"No historical data available for {symbol} {timeframe}")

    # 2. Create StrategyExecutor
    executor = StrategyExecutor(strategy_graph)

    # 3. Event-driven simulation (same logic as vectorbt but structured as events)
    cash = float(starting_capital)
    position_qty = 0.0
    position_side = None
    entry_price = 0.0
    stop_loss = None
    take_profit = None

    trade_history: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []

    equity_curve.append({
        "timestamp": df.iloc[0]["timestamp"],
        "value": round(cash, 2),
    })

    for i in range(len(df)):
        candle = df.iloc[i]
        current_price = float(candle["close"])

        market_context = {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": df.iloc[:i+1].copy(),
            "current_price": current_price,
            "sentiment": None,
        }

        signals = executor.evaluate(market_context)

        for signal in signals:
            if signal.action in ("buy", "sell"):
                side = signal.action
                params = signal.params
                amount = float(params.get("amount", 0.1))

                if side == "buy" and position_side != "long":
                    if position_side == "short" and position_qty > 0:
                        close_price = current_price
                        pnl = (entry_price - close_price) * position_qty
                        fee = close_price * position_qty * (fees_pct / 100.0)
                        net_pnl = pnl - fee
                        cash += net_pnl
                        trade_history.append({
                            "timestamp": candle["timestamp"],
                            "side": "buy",
                            "price": round(close_price, 2),
                            "quantity": round(position_qty, 8),
                            "pnl": round(net_pnl, 2),
                        })
                        position_qty = 0.0
                        position_side = None

                    position_side = "long"
                    position_qty = amount
                    entry_price = current_price
                    cash -= current_price * amount
                    cash -= current_price * amount * (fees_pct / 100.0)
                    trade_history.append({
                        "timestamp": candle["timestamp"],
                        "side": "buy",
                        "price": round(current_price, 2),
                        "quantity": round(amount, 8),
                        "pnl": None,
                    })

                    if "stop_loss_pct" in params:
                        stop_loss = entry_price * (1 - float(params["stop_loss_pct"]) / 100.0)
                    if "take_profit_pct" in params:
                        take_profit = entry_price * (1 + float(params["take_profit_pct"]) / 100.0)

                elif side == "sell" and position_side != "short":
                    if position_side == "long" and position_qty > 0:
                        close_price = current_price
                        pnl = (close_price - entry_price) * position_qty
                        fee = close_price * position_qty * (fees_pct / 100.0)
                        net_pnl = pnl - fee
                        cash += net_pnl
                        trade_history.append({
                            "timestamp": candle["timestamp"],
                            "side": "sell",
                            "price": round(close_price, 2),
                            "quantity": round(position_qty, 8),
                            "pnl": round(net_pnl, 2),
                        })
                        position_qty = 0.0
                        position_side = None

                    position_side = "short"
                    position_qty = amount
                    entry_price = current_price
                    cash += current_price * amount
                    cash -= current_price * amount * (fees_pct / 100.0)
                    trade_history.append({
                        "timestamp": candle["timestamp"],
                        "side": "sell",
                        "price": round(current_price, 2),
                        "quantity": round(amount, 8),
                        "pnl": None,
                    })

                    if "stop_loss_pct" in params:
                        stop_loss = entry_price * (1 + float(params["stop_loss_pct"]) / 100.0)
                    if "take_profit_pct" in params:
                        take_profit = entry_price * (1 - float(params["take_profit_pct"]) / 100.0)

            elif signal.action == "set_stop_loss":
                params = signal.params
                if "pct" in params:
                    if position_side == "long":
                        stop_loss = entry_price * (1 - float(params["pct"]) / 100.0)
                    elif position_side == "short":
                        stop_loss = entry_price * (1 + float(params["pct"]) / 100.0)
                elif "price" in params:
                    stop_loss = float(params["price"])

            elif signal.action == "set_take_profit":
                params = signal.params
                if "pct" in params:
                    if position_side == "long":
                        take_profit = entry_price * (1 + float(params["pct"]) / 100.0)
                    elif position_side == "short":
                        take_profit = entry_price * (1 - float(params["pct"]) / 100.0)
                elif "price" in params:
                    take_profit = float(params["price"])

        # Risk management checks (same as vectorbt)
        if position_side == "long" and position_qty > 0:
            if stop_loss and current_price <= stop_loss:
                close_price = stop_loss
                pnl = (close_price - entry_price) * position_qty
                fee = close_price * position_qty * (fees_pct / 100.0)
                net_pnl = pnl - fee
                cash += net_pnl
                trade_history.append({
                    "timestamp": candle["timestamp"],
                    "side": "sell",
                    "price": round(close_price, 2),
                    "quantity": round(position_qty, 8),
                    "pnl": round(net_pnl, 2),
                })
                position_qty = 0.0
                position_side = None
                stop_loss = None
                take_profit = None

            elif take_profit and current_price >= take_profit:
                close_price = take_profit
                pnl = (close_price - entry_price) * position_qty
                fee = close_price * position_qty * (fees_pct / 100.0)
                net_pnl = pnl - fee
                cash += net_pnl
                trade_history.append({
                    "timestamp": candle["timestamp"],
                    "side": "sell",
                    "price": round(close_price, 2),
                    "quantity": round(position_qty, 8),
                    "pnl": round(net_pnl, 2),
                })
                position_qty = 0.0
                position_side = None
                stop_loss = None
                take_profit = None

        elif position_side == "short" and position_qty > 0:
            if stop_loss and current_price >= stop_loss:
                close_price = stop_loss
                pnl = (entry_price - close_price) * position_qty
                fee = close_price * position_qty * (fees_pct / 100.0)
                net_pnl = pnl - fee
                cash += net_pnl
                trade_history.append({
                    "timestamp": candle["timestamp"],
                    "side": "buy",
                    "price": round(close_price, 2),
                    "quantity": round(position_qty, 8),
                    "pnl": round(net_pnl, 2),
                })
                position_qty = 0.0
                position_side = None
                stop_loss = None
                take_profit = None

            elif take_profit and current_price <= take_profit:
                close_price = take_profit
                pnl = (entry_price - close_price) * position_qty
                fee = close_price * position_qty * (fees_pct / 100.0)
                net_pnl = pnl - fee
                cash += net_pnl
                trade_history.append({
                    "timestamp": candle["timestamp"],
                    "side": "buy",
                    "price": round(close_price, 2),
                    "quantity": round(position_qty, 8),
                    "pnl": round(net_pnl, 2),
                })
                position_qty = 0.0
                position_side = None
                stop_loss = None
                take_profit = None

        if position_side == "long" and position_qty > 0:
            equity = cash + position_qty * current_price
        elif position_side == "short" and position_qty > 0:
            equity = cash - position_qty * current_price
        else:
            equity = cash

        equity_curve.append({
            "timestamp": candle["timestamp"],
            "value": round(equity, 2),
        })

    # Close any open position at end
    if position_qty > 0 and position_side == "long":
        close_price = float(df.iloc[-1]["close"])
        pnl = (close_price - entry_price) * position_qty
        fee = close_price * position_qty * (fees_pct / 100.0)
        net_pnl = pnl - fee
        cash += net_pnl
        trade_history.append({
            "timestamp": df.iloc[-1]["timestamp"],
            "side": "sell",
            "price": round(close_price, 2),
            "quantity": round(position_qty, 8),
            "pnl": round(net_pnl, 2),
        })
    elif position_qty > 0 and position_side == "short":
        close_price = float(df.iloc[-1]["close"])
        pnl = (entry_price - close_price) * position_qty
        fee = close_price * position_qty * (fees_pct / 100.0)
        net_pnl = pnl - fee
        cash += net_pnl
        trade_history.append({
            "timestamp": df.iloc[-1]["timestamp"],
            "side": "buy",
            "price": round(close_price, 2),
            "quantity": round(position_qty, 8),
            "pnl": round(net_pnl, 2),
        })

    metrics = compute_metrics(equity_curve, trade_history, starting_capital)

    return {
        "metrics": metrics,
        "equity_curve": equity_curve,
        "trade_history": trade_history,
    }