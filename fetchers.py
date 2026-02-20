"""
IBKR data extraction functions. Each takes an IB instance + account,
returns a dict suitable for JSON serialization.
"""

import logging
import math
from decimal import Decimal
from typing import Optional

from ib_insync import IB

log = logging.getLogger(__name__)


def _q(val) -> Optional[float]:
    """Quantize a Decimal to 2 places and return float. NaN/None → None."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return float(Decimal(str(val)).quantize(Decimal("0.01")))


def fetch_positions(ib: IB, account: str) -> dict:
    """Pull positions and NLV via reqAccountUpdates (subscription-based)."""
    ib.reqAccountUpdates(True, account)
    ib.sleep(2)
    account_values = ib.accountValues(account=account)
    portfolio_items = ib.portfolio(account=account)

    # Extract NLV — prefer CAD, fall back to USD
    nlv, nlv_currency = None, "CAD"
    for preferred in ("CAD", "USD"):
        for av in account_values:
            if av.tag == "NetLiquidation" and av.currency == preferred:
                nlv, nlv_currency = Decimal(av.value), preferred
                break
        if nlv is not None:
            break
    if nlv is None:
        ib.reqAccountUpdates(False, account)
        raise RuntimeError("Could not determine NetLiquidation value")

    holdings = []
    gross_exposure = Decimal("0")
    for item in portfolio_items:
        if item.position == 0:
            continue
        qty = Decimal(str(item.position))
        mv = Decimal(str(item.marketValue))
        holdings.append({
            "symbol": item.contract.symbol,
            "exchange": item.contract.exchange or item.contract.primaryExchange,
            "currency": item.contract.currency,
            "quantity": int(qty) if qty == int(qty) else float(qty),
            "avg_cost": _q(item.averageCost),
            "market_value": _q(mv),
            "unrealized_pnl": _q(item.unrealizedPNL),
        })
        gross_exposure += abs(mv)

    for h in holdings:
        pct = (Decimal(str(abs(h["market_value"]))) / gross_exposure * 100) if gross_exposure > 0 else Decimal("0")
        h["allocation_pct"] = float(pct.quantize(Decimal("0.01")))
    holdings.sort(key=lambda x: x["allocation_pct"], reverse=True)
    ib.reqAccountUpdates(False, account)

    leverage = float((gross_exposure / nlv).quantize(Decimal("0.01"))) if nlv > 0 else None
    return {
        "net_liquidation_value": _q(nlv), "nlv_currency": nlv_currency,
        "gross_exposure": _q(gross_exposure), "leverage": leverage,
        "position_count": len(holdings), "positions": holdings,
    }


def _parse_summary_tags(summary: list, tags: list, account: str) -> tuple[dict, str]:
    """Extract tag values from accountSummary, preferring CAD currency.
    Returns (values_dict, currency)."""
    values = {}
    currency = "CAD"
    for item in summary:
        if item.account != account or item.tag not in tags:
            continue
        # Cushion is a dimensionless ratio
        if item.tag == "Cushion":
            values[item.tag] = float(item.value)
        elif item.currency in ("CAD", "USD", "BASE"):
            if item.tag not in values or item.currency == "CAD":
                values[item.tag] = float(item.value)
                if item.currency in ("CAD", "USD"):
                    currency = item.currency
    return values, currency


def fetch_margin(ib: IB, account: str) -> dict:
    """Pull margin data via accountSummary."""
    tags = ["InitMarginReq", "MaintMarginReq", "BuyingPower",
            "Cushion", "SMA", "ExcessLiquidity", "EquityWithLoanValue"]
    values, currency = _parse_summary_tags(
        ib.accountSummary(account=account), tags, account)
    return {
        "init_margin_req": values.get("InitMarginReq"),
        "maint_margin_req": values.get("MaintMarginReq"),
        "buying_power": values.get("BuyingPower"),
        "cushion": values.get("Cushion"),
        "sma": values.get("SMA"),
        "excess_liquidity": values.get("ExcessLiquidity"),
        "equity_with_loan_value": values.get("EquityWithLoanValue"),
        "currency": currency,
    }


def fetch_pnl(ib: IB, account: str) -> dict:
    """Pull account-level P&L via reqPnL subscription."""
    pnl_obj = ib.reqPnL(account)
    ib.sleep(2)
    daily, unrealized, realized = pnl_obj.dailyPnL, pnl_obj.unrealizedPnL, pnl_obj.realizedPnL
    ib.cancelPnL(pnl_obj)

    def _clean(val: float) -> Optional[float]:
        """nan means market closed / no data — write null."""
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return None
        return round(val, 2)

    return {"daily_pnl": _clean(daily), "unrealized_pnl": _clean(unrealized),
            "realized_pnl": _clean(realized)}


def fetch_cash(ib: IB, account: str) -> dict:
    """Pull cash balances via accountSummary."""
    tags = ["TotalCashValue", "SettledCash", "AccruedCash"]
    summary = ib.accountSummary(account=account)
    values, currency = _parse_summary_tags(summary, tags, account)

    # Per-currency cash breakdown
    by_currency = {}
    for item in summary:
        if item.account == account and item.tag == "TotalCashValue" and item.currency != "BASE":
            by_currency[item.currency] = float(item.value)

    return {
        "total_cash_value": values.get("TotalCashValue"),
        "settled_cash": values.get("SettledCash"),
        "accrued_cash": values.get("AccruedCash"),
        "currency": currency,
        "by_currency": by_currency or None,
    }


def fetch_open_orders(ib: IB) -> list:
    """Pull open orders from openTrades()."""
    return [{
        "symbol": t.contract.symbol,
        "action": t.order.action,
        "quantity": float(t.order.totalQuantity),
        "order_type": t.order.orderType,
        "limit_price": t.order.lmtPrice if t.order.lmtPrice is not None else None,
        "status": t.orderStatus.status,
        "filled": float(t.orderStatus.filled),
        "remaining": float(t.orderStatus.remaining),
    } for t in ib.openTrades()]


def _fill_to_dict(fill) -> dict:
    """Convert a Fill object to a serializable dict."""
    cr = fill.commissionReport
    return {
        "symbol": fill.contract.symbol,
        "side": fill.execution.side,
        "quantity": float(fill.execution.shares),
        "price": float(fill.execution.price),
        "time_utc": fill.execution.time.isoformat() if fill.execution.time else None,
        "commission": round(cr.commission, 2) if cr else None,
        "commission_currency": cr.currency if cr else None,
    }


def fetch_recent_fills(ib: IB) -> list:
    """Pull today's executions via reqExecutions, fallback to fills()."""
    from ib_insync import ExecutionFilter
    executions = ib.reqExecutions(ExecutionFilter())
    ib.sleep(1)
    source = executions if executions else ib.fills()
    return [_fill_to_dict(f) for f in source]


def _safe_fetch(name: str, fn, *args, **kwargs):
    """Call fn, return result on success or None on failure."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        log.exception("Failed to fetch %s", name)
        return None


def fetch_all(ib: IB, account: str) -> dict:
    """Orchestrate all fetchers. Positions required, rest best-effort."""
    # Positions are mandatory — let this raise
    pos_data = fetch_positions(ib, account)

    margin = _safe_fetch("margin", fetch_margin, ib, account)
    pnl = _safe_fetch("pnl", fetch_pnl, ib, account)
    cash = _safe_fetch("cash", fetch_cash, ib, account)
    open_orders = _safe_fetch("open_orders", fetch_open_orders, ib)
    recent_fills = _safe_fetch("recent_fills", fetch_recent_fills, ib)

    return {
        "account": account,
        "timestamp_utc": None,  # Set by caller
        **pos_data,
        "margin": margin,
        "pnl": pnl,
        "cash": cash,
        "open_orders": open_orders,
        "recent_fills": recent_fills,
        "alerts_triggered": [],  # Set by caller after check_alerts
    }
