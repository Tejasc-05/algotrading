from datetime import datetime, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.enums import OrderSide, OrderStatus, OrderType, TradingMode
from app.database.models.paper_trading import Order, PaperAccount
from app.database.models.user import User
from app.paper_trading.portfolio import apply_fill_to_portfolio, realized_pnl
from app.paper_trading.simulator import simulate_fill


def test_simulate_fill_buy():
    class DummyOrder:
        side = OrderSide.BUY
        quantity = 2.0

    current_price = 100.0
    fill = simulate_fill(DummyOrder(), current_price, fee_pct=0.001, slippage_pct=0.0005)

    # Buy has upward slippage: 100 * (1 + 0.0005) = 100.05
    assert fill["executed_price"] == pytest.approx(100.05)
    assert fill["executed_quantity"] == 2.0
    # Gross: 100.05 * 2 = 200.10. Fee: 200.10 * 0.001 = 0.2001
    assert fill["fee"] == pytest.approx(0.2001)
    assert fill["pnl"] is None


def test_simulate_fill_sell():
    class DummyOrder:
        side = OrderSide.SELL
        quantity = 1.5

    current_price = 200.0
    fill = simulate_fill(DummyOrder(), current_price, fee_pct=0.001, slippage_pct=0.0005)

    # Sell has downward slippage: 200 * (1 - 0.0005) = 199.90
    assert fill["executed_price"] == pytest.approx(199.90)
    assert fill["executed_quantity"] == 1.5
    # Gross: 199.90 * 1.5 = 299.85. Fee: 299.85 * 0.001 = 0.29985
    assert fill["fee"] == pytest.approx(0.29985)


@pytest.mark.asyncio
async def test_apply_fill_to_portfolio_lifecycle(db_session: AsyncSession):
    # Setup test user and paper account
    user = User(email="trader@algoflow.io", hashed_password="pw", full_name="Trader")
    db_session.add(user)
    await db_session.flush()

    account = PaperAccount(
        user_id=user.id,
        starting_balance=10000.0,
        balance=10000.0,
        symbol="BTC/USDT",
    )
    db_session.add(account)
    await db_session.flush()

    # Step 1: Execute BUY of 1.0 BTC at 50,000 with 50 fee
    buy_fill = {
        "executed_price": 50000.0,
        "executed_quantity": 1.0,
        "fee": 50.0,
    }
    state = await apply_fill_to_portfolio(
        db_session, account, symbol="BTC/USDT", side="buy", fill=buy_fill
    )

    # Cost = 50,000 + 50 = 50,050 -> Balance = 10,000 - 50,050 = -40,050
    assert state["cash_balance"] == pytest.approx(-40050.0)
    assert len(state["positions"]) == 1
    pos = state["positions"][0]
    assert pos.symbol == "BTC/USDT"
    assert pos.quantity == 1.0
    assert pos.average_entry_price == 50000.0

    # Step 2: Execute SELL of 1.0 BTC at 55,000 with 55 fee
    sell_fill = {
        "executed_price": 55000.0,
        "executed_quantity": 1.0,
        "fee": 55.0,
    }
    state2 = await apply_fill_to_portfolio(
        db_session, account, symbol="BTC/USDT", side="sell", fill=sell_fill
    )

    # Proceeds = 55,000 - 55 = 54,945 -> Balance = -40,050 + 54,945 = 14,895
    assert state2["cash_balance"] == pytest.approx(14895.0)
    # Net profit = (55,000 - 50,000)*1 - 55 = 4,945
    assert state2["pnl"] == pytest.approx(4945.0)
    # Position should now be closed
    assert len(state2["positions"]) == 0
