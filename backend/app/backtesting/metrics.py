"""Shared metric computations (Sharpe ratio, max drawdown, profit factor,
win rate, equity curve) consumed by both backtesting engines so VectorBT and
Backtrader runs produce identically-shaped, comparable results.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

RFR = 0.0  # risk-free rate for Sharpe (0 = assume cash earns nothing)


def compute_metrics(
    equity_curve: list[dict[str, Any]],
    trade_history: list[dict[str, Any]],
    starting_capital: float,
    risk_free_rate: float = RFR,
) -> dict[str, Any]:
    """Compute standard backtest metrics from an equity curve and trade list.

    Parameters
    ----------
    equity_curve : list[dict]
        Each item has 'timestamp' (datetime) and 'value' (float portfolio value).
    trade_history : list[dict]
        Each item has 'side', 'price', 'quantity', 'pnl'.
    starting_capital : float
        Initial capital deployed.
    risk_free_rate : float
        Annualized risk-free rate for Sharpe (default 0).

    Returns
    -------
    dict with keys: total_return_pct, net_profit, win_rate_pct, num_trades,
    max_drawdown_pct, sharpe_ratio, profit_factor, final_portfolio_value.
    """
    if not equity_curve:
        return {
            "total_return_pct": 0.0,
            "net_profit": 0.0,
            "win_rate_pct": 0.0,
            "num_trades": 0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": None,
            "profit_factor": None,
            "final_portfolio_value": starting_capital,
        }

    values = np.array([float(p["value"]) for p in equity_curve], dtype=float)
    final_value = float(values[-1])

    # Total return
    total_return_pct = ((final_value - starting_capital) / starting_capital) * 100.0
    net_profit = final_value - starting_capital

    # Max drawdown
    running_max = np.maximum.accumulate(values)
    drawdowns = (values - running_max) / np.where(running_max > 0, running_max, 1.0)
    max_drawdown_pct = float(np.min(drawdowns)) * 100.0 if len(drawdowns) else 0.0
    max_drawdown_pct = abs(min(max_drawdown_pct, 0.0))  # report as positive percentage

    # Trade stats
    pnls = [float(t.get("pnl") or 0.0) for t in trade_history]
    num_trades = len(trade_history)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate_pct = (len(wins) / num_trades * 100.0) if num_trades > 0 else 0.0

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    # Sharpe ratio (annualized from periodic returns)
    returns = np.diff(values) / np.where(values[:-1] > 0, values[:-1], 1.0)
    sharpe_ratio = None
    if len(returns) > 1:
        mean_ret = float(np.mean(returns))
        std_ret = float(np.std(returns, ddof=1))
        if std_ret > 0:
            # Annualize assuming 252 trading days; scale by sqrt of bars per year
            # We don't know the bar count per year here, so report per-bar Sharpe
            sharpe_ratio = round((mean_ret - risk_free_rate) / std_ret, 4)

    return {
        "total_return_pct": round(total_return_pct, 4),
        "net_profit": round(net_profit, 2),
        "win_rate_pct": round(win_rate_pct, 2),
        "num_trades": num_trades,
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "sharpe_ratio": sharpe_ratio,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "final_portfolio_value": round(final_value, 2),
    }


def build_equity_curve_from_trades(
    trade_history: list[dict[str, Any]],
    starting_capital: float,
    timestamps: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Build an equity curve by applying trade PnL cumulatively.

    If `timestamps` is provided, the equity curve is sampled at those points
    (one entry per timestamp). Otherwise one entry per trade.
    """
    curve: list[dict[str, Any]] = []
    capital = float(starting_capital)
    curve.append({"timestamp": timestamps[0] if timestamps else None, "value": capital})

    for i, trade in enumerate(trade_history):
        pnl = float(trade.get("pnl") or 0.0)
        capital += pnl
        ts = timestamps[i + 1] if timestamps and i + 1 < len(timestamps) else trade.get("timestamp")
        curve.append({"timestamp": ts, "value": round(capital, 2)})

    return curve