from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from app.market_data.historical import normalize_ohlcv
from app.market_data.indicators import (
    compute_indicator,
    _compute_rsi_series,
    _compute_sma_series,
    _compute_ema_series,
    _compute_macd_df,
    _compute_bollinger_bands_df,
)


def test_normalize_ohlcv_from_raw_records():
    raw_candles = [
        [1700000000000, "50000.5", "51000.0", "49500.0", "50500.2", "12.5"],
        [1700003600000, 50500.2, 52000.0, 50200.0, 51800.0, 15.0],
    ]
    df = normalize_ohlcv(raw_candles)

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df["open"].dtype == np.float64
    assert df["close"].iloc[0] == 50500.2
    assert isinstance(df["timestamp"].iloc[0], pd.Timestamp)


def test_normalize_ohlcv_from_dataframe():
    input_df = pd.DataFrame({
        "time": ["2026-01-01 00:00:00", "2026-01-01 01:00:00"],
        "open": [100, 105],
        "high": [110, 115],
        "low": [95, 100],
        "close": [105, 110],
        "volume": [1000, 1500],
    })
    df = normalize_ohlcv(input_df)

    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df["close"].iloc[1] == 110.0


def test_compute_indicators():
    prices = np.array([10.0 + i * 0.5 for i in range(40)])
    high = prices + 1.0
    low = prices - 1.0
    volume = np.full(40, 500.0)

    df = pd.DataFrame({
        "open": prices,
        "high": high,
        "low": low,
        "close": prices,
        "volume": volume,
    })

    sma_20 = compute_indicator("sma", df, {"period": 20})
    assert len(sma_20) == 40
    assert not np.isnan(sma_20.iloc[-1])
    assert sma_20.iloc[-1] < prices[-1]  # In upward trend, SMA is below latest price

    ema_10 = compute_indicator("ema", df, {"period": 10})
    assert len(ema_10) == 40
    assert not np.isnan(ema_10.iloc[-1])

    macd_res = compute_indicator("macd", df, {"fast": 12, "slow": 26, "signal": 9})
    assert "macd" in macd_res.columns
    assert "signal" in macd_res.columns
    assert "histogram" in macd_res.columns

    bb_res = compute_indicator("bollinger_bands", df, {"period": 20, "std_dev": 2.0})
    assert "upper" in bb_res.columns
    assert "middle" in bb_res.columns
    assert "lower" in bb_res.columns
    assert (bb_res["upper"].dropna() >= bb_res["middle"].dropna()).all()
    assert (bb_res["middle"].dropna() >= bb_res["lower"].dropna()).all()
