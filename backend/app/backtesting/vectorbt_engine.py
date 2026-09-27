"""Primary vectorized backtesting engine (VectorBT-like implementation without
external dependency). Uses the StrategyExecutor to evaluate the strategy on
each bar, simulates fills, and returns standardized metrics.

This is a lightweight, self-contained implementation — no VectorBT package
required. It computes the same metrics the vectorbt package would produce
by running the strategy bar-by-bar with full position tracking.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.backtesting.metrics import compute_metrics
from app.database.models.enums import TradingMode
from app.market_data.historical import fetch_historical_ohlcv
from app.paper_trading.simulator import DEFAULT_FEE_PCT, DEFAULT_SLIPPAGE_PCT, simulate_fill
from app.strategy_engine.executor import StrategyExecutor, Signal


async def run_vectorbt_backtest(
    strategy_graph: Any,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    starting_capital: float,
    fees_pct: float,
    slippage_pct: float,
) -> dict[str, Any]:
    """Run a vectorized backtest against historical OHLCV data.

    Returns a dict matching `BacktestResultOut` schema:
        - metrics (BacktestMetricsOut fields)
        - equity_curve (list of {timestamp, value})
        - trade_history (list of {timestamp, side, price, quantity, pnl})
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

    # 3. Simulate bar-by-bar
    cash = float(starting_capital)
    position_qty = 0.0
    position_side = None  # "long" or "short"
    entry_price = 0.0
    stop_loss = None
    take_profit = None

    trade_history: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []

    # Initial equity point
    equity_curve.append({
        "timestamp": df.iloc[0]["timestamp"],
        "value": round(cash, 2),
    })

    # Iterate through each candle
    for i in range(len(df)):
        candle = df.iloc[i]
        current_price = float(candle["close"])

        # Build market context for this bar
        market_context = {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": df.iloc[:i+1].copy(),
            "current_price": current_price,
            "sentiment": None,
        }

        # Execute strategy on this bar
        signals = executor.evaluate(market_context)

        # Process signals
        for signal in signals:
            if signal.action in ("buy", "sell"):
                side = signal.action
                params = signal.params
                amount = float(params.get("amount", 0.1))
                order_type = params.get("type", "market")

                # Determine position side
                if side == "buy" and position_side != "long":
                    # Close existing short if any
                    if position_side == "short" and position_qty > 0:
                        # Close short
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

                    # Open long
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

                    # Apply risk params
                    if "stop_loss_pct" in params:
                        stop_loss = entry_price * (1 - float(params["stop_loss_pct"]) / 100.0)
                    if "take_profit_pct" in params:
                        take_profit = entry_price * (1 + float(params["take_profit_pct"]) / 100.0)

                elif side == "sell" and position_side != "short":
                    # Close existing long if any
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

                    # Open short (simulated - just track for PnL)
                    position_side = "short"
                    position_qty = amount
                    entry_price = current_price
                    cash += current_price * amount  # Short proceeds
                    cash -= current_price * amount * (fees_pct / 100.0)

                    trade_history.append({
                        "timestamp": candle["timestamp"],
                        "side": "sell",
                        "price": round(current_price, 2),
                        "quantity": round(amount, 8),
                        "pnl": None,
                    })

                    # Apply risk params
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

        # Check stop loss / take profit
        if position_side == "long" and position_qty > 0:
            if stop_loss and current_price <= stop_loss:
                # Stop loss hit
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
                # Take profit hit
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

        # Equity at end of bar (mark-to-market)
        if position_side == "long" and position_qty > 0:
            equity = cash + position_qty * current_price
        elif position_side == "short" and position_qty > 0:
            equity = cash - position_qty * current_price  # short: profit if price goes down
        else:
            equity = cash

        equity_curve.append({
            "timestamp": candle["timestamp"],
            "value": round(equity, 2),
        })

    # Close any open position at the end
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

    # Compute metrics
    metrics = compute_metrics(equity_curve, trade_history, starting_capital)

    return {
        "metrics": metrics,
        "equity_curve": equity_curve,
        "trade_history": trade_history,
    }