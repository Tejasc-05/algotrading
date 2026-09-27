"""Strategy CRUD + validation. A strategy's React Flow graph is never
mutated in place — every save creates a new immutable StrategyVersion row
(see app.database.models.strategy), so past versions are never destroyed.
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, get_db
from app.core.exceptions import NotFoundError
from app.database.models.strategy import Strategy, StrategyVersion
from app.database.models.user import User
from app.database.repositories.strategy_repository import StrategyRepository
from app.schemas.strategy import (
    StrategyCreate,
    StrategyDetailOut,
    StrategyGraph,
    StrategySummaryOut,
    StrategyUpdate,
    StrategyValidateRequest,
    StrategyValidationResult,
    StrategyVersionOut,
)
from app.strategy_engine.validator import validate_strategy_graph

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _current_version_number(strategy: Strategy) -> int:
    return max((v.version_number for v in strategy.versions), default=0)


def _to_summary(strategy: Strategy) -> StrategySummaryOut:
    return StrategySummaryOut(
        id=strategy.id,
        name=strategy.name,
        description=strategy.description,
        current_version=_current_version_number(strategy),
        created_at=strategy.created_at,
        updated_at=strategy.updated_at,
    )


def _to_detail(strategy: Strategy) -> StrategyDetailOut:
    versions = [
        StrategyVersionOut(
            id=v.id,
            version_number=v.version_number,
            graph=StrategyGraph.model_validate(v.graph_json),
            created_at=v.created_at,
        )
        for v in sorted(strategy.versions, key=lambda v: v.version_number)
    ]
    return StrategyDetailOut(
        id=strategy.id,
        name=strategy.name,
        description=strategy.description,
        current_version=_current_version_number(strategy),
        created_at=strategy.created_at,
        updated_at=strategy.updated_at,
        versions=versions,
    )


async def _get_owned_strategy(db: AsyncSession, strategy_id: str, user_id: str) -> Strategy:
    strategy = await StrategyRepository(db).get_with_versions(strategy_id)
    # 404 (not 403) for strategies owned by another user, so existence isn't leaked.
    if strategy is None or strategy.user_id != user_id:
        raise NotFoundError("Strategy not found", code="STRATEGY_NOT_FOUND")
    return strategy


@router.post("", response_model=StrategyDetailOut, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    payload: StrategyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StrategyDetailOut:
    repo = StrategyRepository(db)
    strategy = Strategy(user_id=current_user.id, name=payload.name, description=payload.description)
    repo.add(strategy)
    await repo.flush()  # assign strategy.id before the version row references it

    version = StrategyVersion(
        strategy_id=strategy.id,
        version_number=1,
        graph_json=payload.graph.model_dump(by_alias=True),
        created_by_user_id=current_user.id,
    )
    repo.add_version(version)
    await repo.commit()

    strategy = await repo.get_with_versions(strategy.id)
    return _to_detail(strategy)


@router.get("", response_model=list[StrategySummaryOut])
async def list_strategies(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[StrategySummaryOut]:
    strategies = await StrategyRepository(db).list_for_user(current_user.id)
    return [_to_summary(s) for s in strategies]


@router.get("/{strategy_id}", response_model=StrategyDetailOut)
async def get_strategy(
    strategy_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> StrategyDetailOut:
    strategy = await _get_owned_strategy(db, strategy_id, current_user.id)
    return _to_detail(strategy)


@router.put("/{strategy_id}", response_model=StrategyDetailOut)
async def update_strategy(
    strategy_id: str,
    payload: StrategyUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StrategyDetailOut:
    repo = StrategyRepository(db)
    strategy = await _get_owned_strategy(db, strategy_id, current_user.id)

    if payload.name is not None:
        strategy.name = payload.name
    if payload.description is not None:
        strategy.description = payload.description

    next_version_number = await repo.next_version_number(strategy.id)
    version = StrategyVersion(
        strategy_id=strategy.id,
        version_number=next_version_number,
        graph_json=payload.graph.model_dump(by_alias=True),
        created_by_user_id=current_user.id,
    )
    repo.add_version(version)
    await repo.commit()

    strategy = await repo.get_with_versions(strategy.id)
    return _to_detail(strategy)


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_strategy(
    strategy_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    repo = StrategyRepository(db)
    strategy = await _get_owned_strategy(db, strategy_id, current_user.id)
    await repo.delete(strategy)
    await repo.commit()


@router.post("/{strategy_id}/validate", response_model=StrategyValidationResult)
async def validate_strategy(
    strategy_id: str,
    payload: StrategyValidateRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StrategyValidationResult:
    strategy = await _get_owned_strategy(db, strategy_id, current_user.id)

    if payload is not None and payload.graph is not None:
        graph = payload.graph
    else:
        if not strategy.versions:
            raise NotFoundError("Strategy has no saved versions to validate", code="NO_VERSIONS")
        latest = max(strategy.versions, key=lambda v: v.version_number)
        graph = StrategyGraph.model_validate(latest.graph_json)

    return validate_strategy_graph(graph)


@router.post("/{strategy_id}/execute", response_model=dict)
async def execute_strategy(
    strategy_id: str,
    payload: dict | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Execute a strategy against latest market data and return signals."""
    from app.strategy_engine.executor import StrategyExecutor

    # Get the strategy graph
    strategy = await _get_owned_strategy(db, strategy_id, current_user.id)

    # Get latest version graph
    latest = max(strategy.versions, key=lambda v: v.version_number)
    from app.schemas.strategy import StrategyGraph
    graph = StrategyGraph.model_validate(latest.graph_json)

    # Create executor and evaluate
    executor = StrategyExecutor(graph)

    # Fetch latest market data (use Binance)
    from app.market_data.ccxt_client import get_ccxt_client
    client = get_ccxt_client("binance")
    candles_df = await client._exchange.fetch_ohlcv(
        symbol="BTC/USDT",
        timeframe="1h",
        limit=100,
    )

    import pandas as pd
    candles_pd = pd.DataFrame(
        candles_df,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )

    market_context = {
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candles": candles_pd,
        "current_price": float(candles_df[-1][4]),  # close price
        "sentiment": None,
    }

    # Evaluate strategy
    signals = executor.evaluate(market_context)

    return {
        "strategy_id": strategy.id,
        "signals": [
            {
                "node_id": s.node_id,
                "action": s.action,
                "params": s.params,
            }
            for s in signals
        ],
        "candle_count": len(candles_df),
    }
