"""Indicator math (RSI, SMA, EMA, MACD, Bollinger Bands) over OHLCV series."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _as_array(close: pd.Series | np.ndarray) -> np.ndarray:
    return np.asarray(close, dtype=float)


def rsi(close: pd.Series | np.ndarray, period: int) -> pd.Series:
    prices = _as_array(close)
    values = np.full(len(prices), np.nan)
    if len(prices) < period + 1:
        return pd.Series(values, index=getattr(close, "index", None))

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.full(len(prices), np.nan)
    avg_loss = np.full(len(prices), np.nan)
    avg_gain[period] = np.mean(gains[:period])
    avg_loss[period] = np.mean(losses[:period])

    alpha = 1.0 / period
    for i in range(period + 1, len(prices)):
        avg_gain[i] = alpha * gains[i - 1] + (1 - alpha) * avg_gain[i - 1]
        avg_loss[i] = alpha * losses[i - 1] + (1 - alpha) * avg_loss[i - 1]

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss == 0, np.where(avg_gain > 0, np.inf, 1.0), avg_gain / avg_loss)
        values = np.where(np.isinf(rs), 100.0, 100.0 - (100.0 / (1.0 + rs)))
        values = np.where(np.isnan(avg_gain) | np.isnan(avg_loss), np.nan, values)

    return pd.Series(values, index=getattr(close, "index", None))


def sma(close: pd.Series | np.ndarray, period: int) -> pd.Series:
    values = _as_array(close)
    out = np.full(len(values), np.nan)
    if len(values) >= period:
        kernel = np.ones(period) / period
        out[period - 1 :] = np.convolve(values, kernel, mode="valid")
    return pd.Series(out, index=getattr(close, "index", None))


def ema(close: pd.Series | np.ndarray, period: int) -> pd.Series:
    values = _as_array(close)
    out = np.full(len(values), np.nan)
    if len(values) >= period:
        alpha = 2.0 / (period + 1)
        out[period - 1] = np.mean(values[:period])
        for i in range(period, len(values)):
            out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return pd.Series(out, index=getattr(close, "index", None))


def macd(close: pd.Series | np.ndarray, fast: int, slow: int, signal: int) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line.fillna(0), signal)
    histogram = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "histogram": histogram})


def bollinger_bands(close: pd.Series | np.ndarray, period: int, std_dev: float) -> pd.DataFrame:
    middle = sma(close, period)
    values = _as_array(close)
    std = np.full(len(values), np.nan)
    if len(values) >= period:
        for i in range(period - 1, len(values)):
            std[i] = np.std(values[i - period + 1 : i + 1], ddof=0)
    std_series = pd.Series(std, index=middle.index)
    return pd.DataFrame(
        {
            "upper": middle + std_dev * std_series,
            "middle": middle,
            "lower": middle - std_dev * std_series,
        }
    )
