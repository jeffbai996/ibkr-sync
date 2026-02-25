"""
Shared fixtures and IBKR object factories for the test suite.

Uses SimpleNamespace to mimic ib_insync data objects without needing
a live TWS connection. Each factory mirrors the attribute structure
that fetchers.py actually reads.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure project root is importable (fetchers, alerts, sync_portfolio)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── IBKR Object Factories ───────────────────────────────


def make_account_value(
    tag: str,
    value: str,
    currency: str = "CAD",
    account: str = "U18542138",
) -> SimpleNamespace:
    """Mimics ib_insync AccountValue."""
    return SimpleNamespace(
        tag=tag, value=value, currency=currency,
        account=account, modelCode="",
    )


def make_contract(
    symbol: str = "NVDA",
    exchange: str = "SMART",
    primary_exchange: str = "NASDAQ",
    currency: str = "USD",
) -> SimpleNamespace:
    """Mimics ib_insync Contract."""
    return SimpleNamespace(
        symbol=symbol, exchange=exchange,
        primaryExchange=primary_exchange, currency=currency,
    )


def make_portfolio_item(
    symbol: str = "NVDA",
    position: float = 100,
    market_value: float = 15000.0,
    avg_cost: float = 120.0,
    unrealized_pnl: float = 3000.0,
    currency: str = "USD",
    exchange: str = "SMART",
    primary_exchange: str = "NASDAQ",
) -> SimpleNamespace:
    """Mimics ib_insync PortfolioItem."""
    return SimpleNamespace(
        contract=make_contract(symbol, exchange, primary_exchange, currency),
        position=position,
        marketPrice=market_value / position if position != 0 else 0,
        marketValue=market_value,
        averageCost=avg_cost,
        unrealizedPNL=unrealized_pnl,
        realizedPNL=0.0,
        account="U18542138",
    )


def make_trade(
    symbol: str = "NVDA",
    action: str = "BUY",
    quantity: float = 10,
    order_type: str = "LMT",
    lmt_price: float = 150.0,
    status: str = "PreSubmitted",
    filled: float = 0,
    remaining: float = 10,
) -> SimpleNamespace:
    """Mimics ib_insync Trade."""
    return SimpleNamespace(
        contract=make_contract(symbol),
        order=SimpleNamespace(
            action=action, totalQuantity=quantity,
            orderType=order_type, lmtPrice=lmt_price,
        ),
        orderStatus=SimpleNamespace(
            status=status, filled=filled, remaining=remaining,
        ),
    )


def make_fill(
    symbol: str = "NVDA",
    side: str = "BOT",
    shares: float = 100,
    price: float = 150.0,
    time=None,
    commission: float = 1.0,
    commission_currency: str = "USD",
) -> SimpleNamespace:
    """Mimics ib_insync Fill."""
    exec_time = time or datetime(2026, 2, 27, 15, 30, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        contract=make_contract(symbol),
        execution=SimpleNamespace(
            side=side, shares=shares, price=price, time=exec_time,
        ),
        commissionReport=SimpleNamespace(
            commission=commission, currency=commission_currency,
        ),
    )


# ── Pytest fixtures ──────────────────────────────────────


@pytest.fixture
def account():
    return "U18542138"


@pytest.fixture
def nlv_cad():
    """CAD NLV AccountValue."""
    return make_account_value("NetLiquidation", "100000.00", "CAD")


@pytest.fixture
def nlv_usd():
    """USD NLV AccountValue."""
    return make_account_value("NetLiquidation", "75000.00", "USD")
