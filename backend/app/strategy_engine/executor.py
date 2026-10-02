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
from app.strategy_engine import conditions as condition_ops
from app.strategy_engine import indicators as indicator_ops
from app.strategy_engine.nodes.base import NodeCategory, NodeDefinition
from app.strategy_engine.nodes.registry import get_node_definition
from app.strategy_engine.parser import ParsedNode, parse_graph
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
        node: ParsedNode,
        definition: NodeDefinition,
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

    def _evaluate_data_node(self, node: ParsedNode, market_context: dict[str, Any]) -> Any:
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
        self, node: ParsedNode, inputs: list[Any], market_context: dict[str, Any]
    ) -> Any:
        """Indicator nodes compute from upstream (usually price) or directly from candles."""
        source_series = self._to_float_series(inputs[0]) if inputs else None
        if source_series is None:
            df: pd.DataFrame = market_context.get("candles")
            if df is None or df.empty:
                return None
            source_series = df["close"].values

        arr = np.asarray(source_series, dtype=float)
        series = pd.Series(arr)

        if node.node_type == "rsi":
            period = int(node.params.get("period", 14))
            return indicator_ops.rsi(series, period).to_numpy()

        if node.node_type in ("sma", "movingAverage"):
            period = int(node.params.get("period", 50))
            ma_type = str(node.params.get("type", "SMA")).upper()
            if ma_type == "EMA":
                return indicator_ops.ema(series, period).to_numpy()
            return indicator_ops.sma(series, period).to_numpy()

        if node.node_type == "ema":
            period = int(node.params.get("period", 50))
            return indicator_ops.ema(series, period).to_numpy()

        if node.node_type == "macd":
            fast = int(node.params.get("fast", 12))
            slow = int(node.params.get("slow", 26))
            signal_period = int(node.params.get("signal", 9))
            frame = indicator_ops.macd(series, fast, slow, signal_period)
            return {key: frame[key].to_numpy() for key in frame.columns}

        if node.node_type == "bollinger_bands":
            period = int(node.params.get("period", 20))
            std_dev = float(node.params.get("std_dev", 2.0))
            frame = indicator_ops.bollinger_bands(series, period, std_dev)
            return {key: frame[key].to_numpy() for key in frame.columns}

        return None

    # -------------------------------------------------------------------------
    # SENTIMENT node
    # -------------------------------------------------------------------------

    def _evaluate_sentiment_node(self, node: ParsedNode, market_context: dict[str, Any]) -> Any:
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

    def _evaluate_condition_node(self, node: ParsedNode, inputs: list[Any]) -> Any:
        """Condition nodes compare upstream series against a value or another series."""
        op = str(node.params.get("operator", "<"))
        value = node.params.get("value")

        if op in ("crosses_above", "crosses_below"):
            if len(inputs) < 2:
                return np.array([], dtype=bool)
            left = self._to_float_series(inputs[0])
            right = self._to_float_series(inputs[1])
            fn = condition_ops.crosses_above if op == "crosses_above" else condition_ops.crosses_below
            return fn(left, right).to_numpy()

        if len(inputs) < 1:
            return np.array([], dtype=bool)
        series = self._to_float_series(inputs[0])
        if series is None:
            return np.array([], dtype=bool)
        if value is None:
            return np.zeros(len(series), dtype=bool)
        return condition_ops.compare(op, series, float(value)).to_numpy()

    # -------------------------------------------------------------------------
    # LOGIC nodes: AND, OR, NOT
    # -------------------------------------------------------------------------

    def _evaluate_logic_node(self, node: ParsedNode, inputs: list[Any]) -> Any:
        """Logic nodes combine boolean series."""
        if not inputs:
            return np.array([], dtype=bool)
        if node.node_type == "and":
            return condition_ops.logical_and(*inputs).to_numpy()
        if node.node_type == "or":
            return condition_ops.logical_or(*inputs).to_numpy()
        if node.node_type == "not":
            return condition_ops.logical_not(inputs[0]).to_numpy()
        return np.array([], dtype=bool)

    # -------------------------------------------------------------------------
    # ACTION nodes: BUY, SELL, HOLD
    # -------------------------------------------------------------------------

    def _evaluate_action_node(self, node: ParsedNode, inputs: list[Any]) -> Any:
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

    def _evaluate_risk_node(self, node: ParsedNode, inputs: list[Any]) -> Any:
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

    @staticmethod
    def _to_float_series(value: Any) -> np.ndarray | None:
        if value is None:
            return None
        if isinstance(value, dict):
            if "macd" in value:
                value = value["macd"]
            elif "middle" in value:
                value = value["middle"]
            else:
                value = next(iter(value.values()), None)
        if value is None:
            return None
        return np.asarray(value, dtype=float)

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