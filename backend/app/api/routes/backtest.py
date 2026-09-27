"""Backtesting endpoints. Accepts a strategy, runs it against historical data,
and returns standardized metrics (Sharpe, max drawdown, win rate, etc.)."""
from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_db
from app.backtesting.backtrader_engine import run_backtrader_backtest
from app.backtesting.vectorbt_engine import run_vectorbt_backtest
from app.core.exceptions import AppError
from app.database.models.enums import BacktestStatus
from app.schemas.backtest import BacktestRequest
from app.schemas.backtest import BacktestResultOut
from app.schemas.strategy import StrategyGraph

if TYPE_CHECKING:
    from app.database.models.user import User

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post("", response_model=BacktestResultOut, status_code=status.HTTP_202_ACCEPTED)
async def run_backtest(
    payload: BacktestRequest,
    current_user: "User",
    db: AsyncSession,
) -> BacktestResultOut:
    """Run a backtest for a strategy against historical data."""
    from app.database.repositories.strategy_repository import StrategyRepository

    # Get strategy (validate ownership)
    repo = StrategyRepository(db)
    strategy = await repo.get(payload.strategy_id)
    if strategy is None or strategy.user_id != current_user.id:
        raise AppError(code="STRATEGY_NOT_FOUND", message="Strategy not found", status_code=404)

    # Get the specified version (or latest if not specified)
    target_version = payload.strategy_version or max((v.version_number for v in strategy.versions), default=1)
    version = None
    for v in strategy.versions:
        if v.version_number == target_version:
            version = v
            break

    if version is None:
        raise AppError(code="VERSION_NOT_FOUND", message="Strategy version not found", status_code=404)

    graph = StrategyGraph.model_validate(version.graph_json)

    # Convert percentages to decimals
    fees_decimal = payload.fees_pct / 100.0
    slippage_decimal = payload.slippage_pct / 100.0

    # Run backtest based on engine selection
    if payload.engine == "vectorbt":
        result = await run_vectorbt_backtest(
            strategy_graph=graph,
            symbol=payload.symbol,
            timeframe=payload.timeframe,
            start=payload.start_date,
            end=payload.end_date,
            starting_capital=payload.starting_capital,
            fees_pct=fees_decimal,
            slippage_pct=slippage_decimal,
        )
    else:
        result = await run_backtrader_backtest(
            strategy_graph=graph,
            symbol=payload.symbol,
            timeframe=payload.timeframe,
            start=payload.start_date,
            end=payload.end_date,
            starting_capital=payload.starting_capital,
            fees_pct=fees_decimal,
            slippage_pct=slippage_decimal,
        )

    # Build response
    equity_curve = [
        {"timestamp": ec["timestamp"], "value": float(ec["value"])}
        for ec in result["equity_curve"]
    ]
    trade_history = [
        {
            "timestamp": th["timestamp"],
            "side": th["side"],
            "price": float(th["price"]),
            "quantity": float(th["quantity"]),
            "pnl": float(th["pnl"]) if th["pnl"] is not None else None,
        }
        for th in result["trade_history"]
    ]

    return BacktestResultOut(
        id=f"bt_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        status=BacktestStatus.COMPLETED,
        metrics=result["metrics"],
        equity_curve=equity_curve,
        trade_history=trade_history,
    )


@router.get("/{backtest_id}", response_model=BacktestResultOut)
async def get_backtest(
    backtest_id: str, current_user: "User"
) -> BacktestResultOut:
    """Get backtest results by ID (MVP returns placeholder)."""
    raise AppError(
        code="NOT_IMPLEMENTED",
        message="Backtest history not yet persisted - create a new backtest with POST",
        status_code=501,
    )