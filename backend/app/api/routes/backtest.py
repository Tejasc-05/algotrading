"""Backtesting endpoints. Accepts a strategy, runs it against historical data,
and returns standardized metrics (Sharpe, max drawdown, win rate, etc.)."""
from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_db
from app.backtesting.backtrader_engine import run_backtrader_backtest
from app.backtesting.vectorbt_engine import run_vectorbt_backtest
from app.core.exceptions import AppError
from app.database.models.backtest import Backtest, BacktestResult
from app.database.models.enums import BacktestEngine, BacktestStatus
from app.database.models.user import User
from app.schemas.backtest import BacktestRequest
from app.schemas.backtest import BacktestResultOut
from app.schemas.strategy import StrategyGraph

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post("", response_model=BacktestResultOut, status_code=status.HTTP_202_ACCEPTED)
async def run_backtest(
    payload: BacktestRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BacktestResultOut:
    """Run a backtest for a strategy against historical data."""
    from app.database.repositories.strategy_repository import StrategyRepository

    # Get strategy (validate ownership)
    repo = StrategyRepository(db)
    # Fetch strategy with eager-loaded versions to avoid lazy-loading errors in async context
    strategy = await repo.get_with_versions(payload.strategy_id)
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

    # Build response objects
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

    # Persist to database
    bt = Backtest(
        user_id=current_user.id,
        strategy_version_id=version.id,
        engine=BacktestEngine(payload.engine),
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        start_date=payload.start_date,
        end_date=payload.end_date,
        starting_capital=payload.starting_capital,
        fees=payload.fees_pct,
        slippage=payload.slippage_pct,
        status=BacktestStatus.COMPLETED,
    )
    db.add(bt)
    await db.flush()

    equity_curve_json = [
        {
            "timestamp": ec["timestamp"].isoformat() if isinstance(ec["timestamp"], datetime) else str(ec["timestamp"]),
            "value": float(ec["value"]),
        }
        for ec in equity_curve
    ]
    trade_history_json = [
        {
            "timestamp": th["timestamp"].isoformat() if isinstance(th["timestamp"], datetime) else str(th["timestamp"]),
            "side": th["side"],
            "price": float(th["price"]),
            "quantity": float(th["quantity"]),
            "pnl": float(th["pnl"]) if th["pnl"] is not None else None,
        }
        for th in trade_history
    ]

    metrics_data = result["metrics"]
    bt_res = BacktestResult(
        backtest_id=bt.id,
        total_return_pct=metrics_data["total_return_pct"],
        net_profit=metrics_data["net_profit"],
        win_rate_pct=metrics_data["win_rate_pct"],
        num_trades=metrics_data["num_trades"],
        max_drawdown_pct=metrics_data["max_drawdown_pct"],
        sharpe_ratio=metrics_data.get("sharpe_ratio"),
        profit_factor=metrics_data.get("profit_factor"),
        final_portfolio_value=metrics_data["final_portfolio_value"],
        equity_curve_json=equity_curve_json,
        trade_history_json=trade_history_json,
    )
    db.add(bt_res)
    await db.commit()

    return BacktestResultOut(
        id=bt.id,
        status=bt.status,
        metrics=metrics_data,
        equity_curve=equity_curve,
        trade_history=trade_history,
    )


@router.get("/{backtest_id}", response_model=BacktestResultOut)
async def get_backtest(
    backtest_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BacktestResultOut:
    """Get backtest results by ID."""
    stmt = (
        select(Backtest)
        .options(joinedload(Backtest.result))
        .where(Backtest.id == backtest_id, Backtest.user_id == current_user.id)
    )
    res = await db.execute(stmt)
    bt = res.scalars().first()
    if not bt or not bt.result:
        raise AppError(code="BACKTEST_NOT_FOUND", message="Backtest not found", status_code=404)

    r = bt.result
    equity_curve = [
        {
            "timestamp": datetime.fromisoformat(ec["timestamp"]) if isinstance(ec["timestamp"], str) else ec["timestamp"],
            "value": float(ec["value"]),
        }
        for ec in r.equity_curve_json
    ]
    trade_history = [
        {
            "timestamp": datetime.fromisoformat(th["timestamp"]) if isinstance(th["timestamp"], str) else th["timestamp"],
            "side": th["side"],
            "price": float(th["price"]),
            "quantity": float(th["quantity"]),
            "pnl": float(th["pnl"]) if th["pnl"] is not None else None,
        }
        for th in r.trade_history_json
    ]
    metrics = {
        "total_return_pct": float(r.total_return_pct),
        "net_profit": float(r.net_profit),
        "win_rate_pct": float(r.win_rate_pct),
        "num_trades": int(r.num_trades),
        "max_drawdown_pct": float(r.max_drawdown_pct),
        "sharpe_ratio": float(r.sharpe_ratio) if r.sharpe_ratio is not None else None,
        "profit_factor": float(r.profit_factor) if r.profit_factor is not None else None,
        "final_portfolio_value": float(r.final_portfolio_value),
    }

    return BacktestResultOut(
        id=bt.id,
        status=bt.status,
        metrics=metrics,
        equity_curve=equity_curve,
        trade_history=trade_history,
    )