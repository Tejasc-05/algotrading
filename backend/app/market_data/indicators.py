"""Vectorized (pandas/NumPy) indicator computation over full OHLCV
DataFrames, as opposed to app.strategy_engine.indicators which computes a
single node's series during strategy execution. Implemented for
bulk backtesting use.

All functions accept a DataFrame with columns: open, high, low, close, volume
and return a Series or DataFrame with the computed indicator values.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def compute_indicator(name: str, df: pd.DataFrame, params: dict[str, Any]) -> pd.Series | pd.DataFrame:
    """Compute a named indicator over the full DataFrame.

    Parameters
    ----------
    name : str
        Indicator name: 'rsi', 'sma', 'ema', 'macd', 'bollinger_bands', 'atr'
    df : pd.DataFrame
        OHLCV DataFrame with columns: open, high, low, close, volume
    params : dict
        Indicator-specific parameters.

    Returns
    -------
    pd.Series or pd.DataFrame
        Computed indicator values aligned with df index.
    """
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df["volume"].values

    if name == "rsi":
        period = params.get("period", 14)
        return _compute_rsi_series(close, period)

    if name == "sma" or name == "movingAverage":
        period = params.get("period", 50)
        ma_type = params.get("type", "SMA")
        if ma_type == "EMA":
            return _compute_ema_series(close, period)
        return _compute_sma_series(close, period)

    if name == "ema":
        period = params.get("period", 50)
        return _compute_ema_series(close, period)

    if name == "macd":
        fast = params.get("fast", 12)
        slow = params.get("slow", 26)
        signal_period = params.get("signal", 9)
        return _compute_macd_df(close, fast, slow, signal_period)

    if name == "bollinger_bands":
        period = params.get("period", 20)
        std_dev = params.get("std_dev", 2.0)
        return _compute_bollinger_bands_df(close, period, std_dev)

    if name == "atr":
        period = params.get("period", 14)
        return _compute_atr_series(high, low, close, period)

    if name == "stochastic":
        k_period = params.get("k_period", 14)
        d_period = params.get("d_period", 3)
        return _compute_stochastic_df(high, low, close, k_period, d_period)

    raise ValueError(f"Unknown indicator: {name}")


# -------------------------------------------------------------------------
# Helper functions (same algorithms as StrategyExecutor but vectorized)
# -------------------------------------------------------------------------

def _compute_rsi_series(prices: np.ndarray, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing) — vectorized."""
    deltas = np.diff(prices, prepend=np.nan)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Wilder's smoothing
    alpha = 1.0 / period
    avg_gain = pd.Series(gains).ewm(alpha=alpha, adjust=False).mean()
    avg_loss = pd.Series(losses).ewm(alpha=alpha, adjust=False).mean()

    # First 'period' values use simple average
    avg_gain.iloc[period] = gains[:period].mean()
    avg_loss.iloc[period] = losses[:period].mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi.iloc[:period] = np.nan  # Not enough data
    return rsi


def _compute_sma_series(values: np.ndarray, period: int) -> pd.Series:
    """Simple Moving Average."""
    sma = pd.Series(values).rolling(window=period, min_periods=period).mean()
    return sma


def _compute_ema_series(values: np.ndarray, period: int) -> pd.Series:
    """Exponential Moving Average."""
    ema = pd.Series(values).ewm(span=period, adjust=False, min_periods=period).mean()
    return ema


def _compute_macd_df(
    prices: np.ndarray, fast: int = 12, slow: int = 26, signal_period: int = 9
) -> pd.DataFrame:
    """MACD returns DataFrame with 'macd', 'signal', 'histogram' columns."""
    ema_fast = _compute_ema_series(prices, fast)
    ema_slow = _compute_ema_series(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _compute_ema_series(macd_line.fillna(0).values, signal_period)
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "histogram": histogram},
        index=pd.RangeIndex(len(prices)),
    )


def _compute_bollinger_bands_df(
    prices: np.ndarray, period: int = 20, std_dev: float = 2.0
) -> pd.DataFrame:
    """Bollinger Bands returns DataFrame with 'upper', 'middle', 'lower'."""
    middle = _compute_sma_series(prices, period)
    rolling_std = pd.Series(prices).rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + std_dev * rolling_std
    lower = middle - std_dev * rolling_std
    return pd.DataFrame(
        {"upper": upper, "middle": middle, "lower": lower},
        index=pd.RangeIndex(len(prices)),
    )


def _compute_atr_series(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14
) -> pd.Series:
    """Average True Range."""
    prev_close = np.roll(close, 1)
    prev_close[0] = np.nan

    tr1 = high - low
    tr2 = np.abs(high - prev_close)
    tr3 = np.abs(low - prev_close)
    tr = np.maximum(np.maximum(tr1, tr2), tr3)

    atr = pd.Series(tr).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return atr


def _compute_stochastic_df(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, k_period: int = 14, d_period: int = 3
) -> pd.DataFrame:
    """Stochastic Oscillator returns DataFrame with '%K' and '%D'."""
    lowest_low = pd.Series(low).rolling(window=k_period, min_periods=k_period).min()
    highest_high = pd.Series(high).rolling(window=k_period, min_periods=k_period).max()

    k = 100 * (close - lowest_low) / (highest_high - lowest_low)
    d = k.rolling(window=d_period, min_periods=d_period).mean()

    return pd.DataFrame(
        {"%K": k, "%D": d},
        index=pd.RangeIndex(len(close)),
    )


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all standard indicators at once and return as a wide DataFrame.

    Useful for backtesting where we need all indicators pre-computed.
    """
    indicators = {}

    # RSI
    indicators["rsi"] = _compute_rsi_series(df["close"].values)

    # Moving averages
    indicators["sma_20"] = _compute_sma_series(df["close"].values, 20)
    indicators["sma_50"] = _compute_sma_series(df["close"].values, 50)
    indicators["ema_12"] = _compute_ema_series(df["close"].values, 12)
    indicators["ema_26"] = _compute_ema_series(df["close"].values, 26)

    # MACD
    macd_df = _compute_macd_df(df["close"].values)
    indicators["macd"] = macd_df["macd"]
    indicators["macd_signal"] = macd_df["signal"]
    indicators["macd_histogram"] = macd_df["histogram"]

    # Bollinger Bands
    bb_df = _compute_bollinger_bands_df(df["close"].values)
    indicators["bb_upper"] = bb_df["upper"]
    indicators["bb_middle"] = bb_df["middle"]
    indicators["bb_lower"] = bb_df["lower"]

    # ATR
    indicators["atr_14"] = _compute_atr_series(
        df["high"].values, df["low"].values, df["close"].values, 14
    )

    # Volume indicators
    indicators["volume_sma_20"] = _compute_sma_series(df["volume"].values, 20)

    return pd.DataFrame(indicators, index=df.index)