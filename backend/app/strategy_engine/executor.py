"""Compiles a validated StrategyGraph into a topologically-ordered execution
plan and walks it against a market-data context to produce BUY/SELL/HOLD
signals plus attached risk (stop-loss/take-profit) instructions.
"""
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from app.schemas.strategy import StrategyGraph
from app.strategy_engine.nodes.base import NodeCategory
from app.strategy_engine.nodes.registry import get_node_definition
from app.strategy_engine.parser import parse_graph
from app.strategy_engine.validator import validate_strategy_graph


@dataclass
class Signal:
    node_id: str
    action: str  # "buy" | "sell" | "hold" | "set_stop_loss" | "set_take_profit"
    params: dict[str, Any]


class StrategyExecutor:
    """Constructed from an already-validated StrategyGraph. `evaluate()` is
    called once per bar/tick with the current market-data context.

    Expected `market_context` keys:
        - "symbol": str (e.g. "BTC/USDT")
        - "timeframe": str (e.g. "1h")
        - "candles": pd.DataFrame with columns ["timestamp", "open", "high", "low", "close", "volume"]
        - "current_price": float (latest close price)
        - "sentiment": float | None (optional, -1..1 score)
    """

    def __init__(self, graph: StrategyGraph):
        self.graph = graph
        self._parse_result = parse_graph(graph)

    def evaluate(self, market_context: dict[str, Any]) -> list[Signal]:
        """Evaluate the strategy against market data and return trading signals."""
        # Validate the graph first
        validation = validate_strategy_graph(self.graph)
        if not validation.valid:
            return []

        # Topologically sort nodes for execution order
        execution_order = self._topological_order()

        # Node outputs cache: node_id -> computed value (series or scalar)
        node_outputs: dict[str, Any] = {}
        signals: list[Signal] = []

        for node_id in execution_order:
            node = self._parse_result.graph.nodes[node_id]
            definition = get_node_definition(node.node_type)
            if definition is None:
                continue

            # Gather upstream outputs
            upstream_ids = self._parse_result.graph.incoming.get(node_id, [])
            inputs = [node_outputs.get(up_id) for up_id in upstream_ids if up_id in node_outputs]

            output = self._evaluate_node(node, definition, inputs, market_context)
            node_outputs[node_id] = output

            # If node produced a signal, capture it
            if isinstance(output, Signal):
                signals.append(output)

        return signals

    # -------------------------------------------------------------------------
    # Node evaluation dispatch
    # -------------------------------------------------------------------------

    def _evaluate_node(
        self,
        node: "ParsedNode",
        definition: "NodeDefinition",
        inputs: list[Any],
        market_context: dict[str, Any],
    ) -> Any:
        cat = definition.category

        if cat == NodeCategory.DATA:
            return self._evaluate_data_node(node, market_context)
        if cat == NodeCategory.INDICATOR:
            return self._evaluate_indicator_node(node, inputs, market_context)
        if cat == NodeCategory.SENTIMENT:
            return self._evaluate_sentiment_node(node, market_context)
        if cat == NodeCategory.CONDITION:
            return self._evaluate_condition_node(node, inputs)
        if cat == NodeCategory.LOGIC:
            return self._evaluate_logic_node(node, inputs)
        if cat == NodeCategory.ACTION:
            return self._evaluate_action_node(node, inputs)
        if cat == NodeCategory.RISK:
            return self._evaluate_risk_node(node, inputs)
        return None

    # -------------------------------------------------------------------------
    # DATA nodes: Price, Volume
    # -------------------------------------------------------------------------

    def _evaluate_data_node(self, node: "ParsedNode", market_context: dict[str, Any]) -> Any:
        """DATA nodes return a price/volume series from market data."""
        df: pd.DataFrame = market_context.get("candles")
        if df is None or df.empty:
            return None

        if node.node_type == "price":
            # Return close price series aligned with candle index
            return df["close"].values
        if node.node_type == "volume":
            return df["volume"].values
        return None

    # -------------------------------------------------------------------------
    # INDICATOR nodes: RSI, SMA, EMA, MACD, Bollinger Bands
    # -------------------------------------------------------------------------

    def _evaluate_indicator_node(
        self, node: "ParsedNode", inputs: list[Any], market_context: dict[str, Any]
    ) -> Any:
        """Indicator nodes compute from upstream (usually price) or directly from candles."""
        # Use upstream series if connected, otherwise fall back to market close prices
        source_series = inputs[0] if inputs else None
        if source_series is None:
            df: pd.DataFrame = market_context.get("candles")
            if df is None or df.empty:
                return None
            source_series = df["close"].values

        arr = np.asarray(source_series, dtype=float)

        if node.node_type == "rsi":
            period = int(node.params.get("period", 14))
            return self._compute_rsi(arr, period)

        if node.node_type in ("sma", "movingAverage"):
            period = int(node.params.get("period", 50))
            ma_type = node.params.get("type", "SMA")
            if ma_type == "EMA":
                return self._compute_ema(arr, period)
            return self._compute_sma(arr, period)

        if node.node_type == "ema":
            period = int(node.params.get("period", 50))
            return self._compute_ema(arr, period)

        if node.node_type == "macd":
            fast = int(node.params.get("fast", 12))
            slow = int(node.params.get("slow", 26))
            signal_period = int(node.params.get("signal", 9))
            return self._compute_macd(arr, fast, slow, signal_period)

        if node.node_type == "bollinger_bands":
            period = int(node.params.get("period", 20))
            std_dev = float(node.params.get("std_dev", 2.0))
            return self._compute_bollinger_bands(arr, period, std_dev)

        return None

    # -------------------------------------------------------------------------
    # SENTIMENT node
    # -------------------------------------------------------------------------

    def _evaluate_sentiment_node(self, node: "ParsedNode", market_context: dict[str, Any]) -> Any:
        """Returns the sentiment score from market context (or None)."""
        sentiment = market_context.get("sentiment")
        if sentiment is None:
            return None
        # Apply threshold filter if configured
        threshold = float(node.params.get("threshold", 70)) / 100.0
        label = node.params.get("label")
        if label == "positive" and sentiment < threshold:
            return False
        if label == "negative" and sentiment > -threshold:
            return False
        return sentiment

    # -------------------------------------------------------------------------
    # CONDITION nodes: comparisons + crosses
    # -------------------------------------------------------------------------

    def _evaluate_condition_node(self, node: "ParsedNode", inputs: list[Any]) -> Any:
        """Condition nodes compare upstream series against a value or another series."""
        op = node.params.get("operator")
        value = node.params.get("value")

        if op in ("crosses_above", "crosses_below"):
            # Need two upstream series
            if len(inputs) < 2:
                return np.array([], dtype=bool)
            series_a = np.asarray(inputs[0], dtype=float)
            series_b = np.asarray(inputs[1], dtype=float)
            return self._compute_crosses(series_a, series_b, op)

        # Comparison against a scalar value
        if len(inputs) < 1:
            return np.array([], dtype=bool)
        series = np.asarray(inputs[0], dtype=float)
        if value is None:
            return np.zeros_like(series, dtype=bool)

        val = float(value)
        if op == "<":
            return series < val
        if op == ">":
            return series > val
        if op == "<=":
            return series <= val
        if op == ">=":
            return series >= val
        if op == "==":
            return np.isclose(series, val)
        if op == "!=":
            return ~np.isclose(series, val)
        return np.zeros_like(series, dtype=bool)

    def _compute_crosses(self, a: np.ndarray, b: np.ndarray, op: str) -> np.ndarray:
        """Detect crosses_above / crosses_below between two series."""
        if len(a) < 2 or len(b) < 2:
            return np.zeros(len(a), dtype=bool)

        # Align lengths
        n = min(len(a), len(b))
        a = a[-n:]
        b = b[-n:]

        if op == "crosses_above":
            # a crosses above b: a[i-1] <= b[i-1] and a[i] > b[i]
            return (a[:-1] <= b[:-1]) & (a[1:] > b[1:])
        # crosses_below
        return (a[:-1] >= b[:-1]) & (a[1:] < b[1:])

    # -------------------------------------------------------------------------
    # LOGIC nodes: AND, OR, NOT
    # -------------------------------------------------------------------------

    def _evaluate_logic_node(self, node: "ParsedNode", inputs: list[Any]) -> Any:
        """Logic nodes combine boolean series."""
        if not inputs:
            return np.array([], dtype=bool)

        # Convert all inputs to boolean arrays of same length
        bool_arrays = []
        for inp in inputs:
            arr = np.asarray(inp)
            if arr.dtype != bool:
                arr = arr.astype(bool)
            bool_arrays.append(arr)

        if node.node_type == "and":
            # All inputs must be True (element-wise AND)
            result = bool_arrays[0]
            for arr in bool_arrays[1:]:
                result = result & arr
            return result

        if node.node_type == "or":
            # Any input True (element-wise OR)
            result = bool_arrays[0]
            for arr in bool_arrays[1:]:
                result = result | arr
            return result

        if node.node_type == "not":
            # Invert (expects exactly 1 input)
            return ~bool_arrays[0]

        return np.array([], dtype=bool)

    # -------------------------------------------------------------------------
    # ACTION nodes: BUY, SELL, HOLD
    # -------------------------------------------------------------------------

    def _evaluate_action_node(self, node: "ParsedNode", inputs: list[Any]) -> Any:
        """Action nodes emit a Signal when upstream condition is True."""
        if not inputs:
            return None

        # Upstream should be a boolean series (condition/logic output)
        trigger = np.asarray(inputs[0])
        if trigger.dtype != bool:
            trigger = trigger.astype(bool)

        # Only emit signal on the LAST bar where trigger is True
        if len(trigger) == 0 or not trigger[-1]:
            return None

        action_type = node.node_type  # "buy" | "sell" | "hold"
        params = dict(node.params)

        return Signal(node_id=node.id, action=action_type, params=params)

    # -------------------------------------------------------------------------
    # RISK nodes: Stop Loss, Take Profit
    # -------------------------------------------------------------------------

    def _evaluate_risk_node(self, node: "ParsedNode", inputs: list[Any]) -> Any:
        """Risk nodes attach risk parameters to the most recent action signal."""
        if not inputs:
            return None

        trigger = np.asarray(inputs[0])
        if trigger.dtype != bool:
            trigger = trigger.astype(bool)

        if len(trigger) == 0 or not trigger[-1]:
            return None

        action_type = "set_stop_loss" if node.node_type == "stopLoss" else "set_take_profit"
        params = dict(node.params)

        return Signal(node_id=node.id, action=action_type, params=params)

    # -------------------------------------------------------------------------
    # Indicator computation helpers (vectorized, pandas/NumPy)
    # -------------------------------------------------------------------------

    @staticmethod
    def _compute_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
        """Relative Strength Index (Wilder's smoothing)."""
        if len(prices) < period + 1:
            return np.full(len(prices), np.nan)

        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        # Wilder's smoothing (EMA with alpha = 1/period)
        avg_gain = np.full(len(prices), np.nan)
        avg_loss = np.full(len(prices), np.nan)

        # First average is simple mean
        avg_gain[period] = np.mean(gains[:period])
        avg_loss[period] = np.mean(losses[:period])

        alpha = 1.0 / period
        for i in range(period + 1, len(prices)):
            avg_gain[i] = alpha * gains[i - 1] + (1 - alpha) * avg_gain[i - 1]
            avg_loss[i] = alpha * losses[i - 1] + (1 - alpha) * avg_loss[i - 1]

        rs = avg_gain / np.where(avg_loss == 0, np.nan, avg_loss)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def _compute_sma(values: np.ndarray, period: int) -> np.ndarray:
        """Simple Moving Average."""
        if len(values) < period:
            return np.full(len(values), np.nan)
        sma = np.full(len(values), np.nan)
        # Use convolution for efficiency
        kernel = np.ones(period) / period
        valid = np.convolve(values, kernel, mode="valid")
        sma[period - 1 :] = valid
        return sma

    @staticmethod
    def _compute_ema(values: np.ndarray, period: int) -> np.ndarray:
        """Exponential Moving Average."""
        if len(values) < period:
            return np.full(len(values), np.nan)
        ema = np.full(len(values), np.nan)
        alpha = 2.0 / (period + 1)
        ema[period - 1] = np.mean(values[:period])
        for i in range(period, len(values)):
            ema[i] = alpha * values[i] + (1 - alpha) * ema[i - 1]
        return ema

    @staticmethod
    def _compute_macd(
        prices: np.ndarray, fast: int = 12, slow: int = 26, signal_period: int = 9
    ) -> dict[str, np.ndarray]:
        """MACD returns dict with 'macd', 'signal', 'histogram'."""
        ema_fast = StrategyExecutor._compute_ema(prices, fast)
        ema_slow = StrategyExecutor._compute_ema(prices, slow)
        macd_line = ema_fast - ema_slow
        signal_line = StrategyExecutor._compute_ema(np.nan_to_num(macd_line, nan=0), signal_period)
        histogram = macd_line - signal_line
        return {"macd": macd_line, "signal": signal_line, "histogram": histogram}

    @staticmethod
    def _compute_bollinger_bands(
        prices: np.ndarray, period: int = 20, std_dev: float = 2.0
    ) -> dict[str, np.ndarray]:
        """Bollinger Bands returns dict with 'upper', 'middle', 'lower'."""
        middle = StrategyExecutor._compute_sma(prices, period)
        if len(prices) < period:
            return {"upper": middle, "middle": middle, "lower": middle}

        # Rolling std
        std = np.full(len(prices), np.nan)
        for i in range(period - 1, len(prices)):
            window = prices[i - period + 1 : i + 1]
            std[i] = np.std(window, ddof=0)

        upper = middle + std_dev * std
        lower = middle - std_dev * std
        return {"upper": upper, "middle": middle, "lower": lower}

    # -------------------------------------------------------------------------
    # Topological ordering
    # -------------------------------------------------------------------------

    def _topological_order(self) -> list[str]:
        """Kahn's algorithm for topological sort."""
        nodes = self._parse_result.graph.nodes
        outgoing = self._parse_result.graph.outgoing
        in_degree = {nid: 0 for nid in nodes}
        for src, targets in outgoing.items():
            for tgt in targets:
                if tgt in in_degree:
                    in_degree[tgt] += 1

        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        order: list[str] = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for tgt in outgoing.get(nid, []):
                if tgt in in_degree:
                    in_degree[tgt] -= 1
                    if in_degree[tgt] == 0:
                        queue.append(tgt)

        if len(order) != len(nodes):
            # Cycle (should have been caught by validator)
            return list(nodes.keys())
        return order