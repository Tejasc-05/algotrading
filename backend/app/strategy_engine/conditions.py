"""Condition/logic operator evaluation over indicator series."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _align(left: pd.Series | np.ndarray, right: pd.Series | np.ndarray | float) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(left, dtype=float)
    if isinstance(right, (int, float, np.floating)):
        b = np.full_like(a, float(right))
        return a, b
    b = np.asarray(right, dtype=float)
    n = min(len(a), len(b))
    return a[-n:], b[-n:]


def compare(operator: str, left: pd.Series | np.ndarray, right: pd.Series | float) -> pd.Series:
    a, b = _align(left, right)
    if operator == "<":
        out = a < b
    elif operator == ">":
        out = a > b
    elif operator == "<=":
        out = a <= b
    elif operator == ">=":
        out = a >= b
    elif operator == "==":
        out = np.isclose(a, b)
    elif operator == "!=":
        out = ~np.isclose(a, b)
    else:
        out = np.zeros(len(a), dtype=bool)
    return pd.Series(out)


def crosses_above(left: pd.Series | np.ndarray, right: pd.Series | np.ndarray) -> pd.Series:
    a, b = _align(left, right)
    out = np.zeros(len(a), dtype=bool)
    if len(a) >= 2:
        out[1:] = (a[:-1] <= b[:-1]) & (a[1:] > b[1:])
    return pd.Series(out)


def crosses_below(left: pd.Series | np.ndarray, right: pd.Series | np.ndarray) -> pd.Series:
    a, b = _align(left, right)
    out = np.zeros(len(a), dtype=bool)
    if len(a) >= 2:
        out[1:] = (a[:-1] >= b[:-1]) & (a[1:] < b[1:])
    return pd.Series(out)


def _bool_arrays(*operands: pd.Series | np.ndarray) -> list[np.ndarray]:
    arrays = [np.asarray(op).astype(bool) for op in operands if op is not None]
    if not arrays:
        return []
    n = min(len(a) for a in arrays)
    return [a[-n:] for a in arrays]


def logical_and(*operands: pd.Series | np.ndarray) -> pd.Series:
    arrays = _bool_arrays(*operands)
    if not arrays:
        return pd.Series(np.array([], dtype=bool))
    result = arrays[0]
    for arr in arrays[1:]:
        result = result & arr
    return pd.Series(result)


def logical_or(*operands: pd.Series | np.ndarray) -> pd.Series:
    arrays = _bool_arrays(*operands)
    if not arrays:
        return pd.Series(np.array([], dtype=bool))
    result = arrays[0]
    for arr in arrays[1:]:
        result = result | arr
    return pd.Series(result)


def logical_not(operand: pd.Series | np.ndarray) -> pd.Series:
    return pd.Series(~np.asarray(operand).astype(bool))
