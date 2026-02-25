"""
Tests for fetchers.py — financial math, IBKR data extraction.

fetch_positions is the most critical: it calculates NLV, gross exposure,
leverage, and per-position allocations. All financial math goes through
Decimal to avoid float errors. We mock the IB connection object to feed
controlled data into these functions.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fetchers import (
    _parse_summary_tags,
    _safe_fetch,
    fetch_all,
    fetch_open_orders,
    fetch_pnl,
    fetch_positions,
)

from conftest import (
    make_account_value,
    make_portfolio_item,
    make_trade,
)


# ── _parse_summary_tags (pure function) ──────────────────


class TestParseSummaryTags:

    def test_cad_preferred_over_usd(self):
        summary = [
            make_account_value("InitMarginReq", "50000.00", "USD"),
            make_account_value("InitMarginReq", "68000.00", "CAD"),
        ]
        values, currency = _parse_summary_tags(
            summary, ["InitMarginReq"], "U18542138",
        )
        assert values["InitMarginReq"] == 68000.0
        assert currency == "CAD"

    def test_usd_fallback_when_no_cad(self):
        summary = [
            make_account_value("InitMarginReq", "50000.00", "USD"),
        ]
        values, currency = _parse_summary_tags(
            summary, ["InitMarginReq"], "U18542138",
        )
        assert values["InitMarginReq"] == 50000.0
        assert currency == "USD"

    def test_cushion_ignores_currency(self):
        """Cushion is a dimensionless ratio, stored regardless of currency."""
        summary = [
            make_account_value("Cushion", "0.25", ""),
        ]
        values, currency = _parse_summary_tags(
            summary, ["Cushion"], "U18542138",
        )
        assert values["Cushion"] == 0.25

    def test_wrong_account_filtered(self):
        summary = [
            make_account_value("InitMarginReq", "50000.00", "CAD", account="WRONG"),
        ]
        values, _ = _parse_summary_tags(
            summary, ["InitMarginReq"], "U18542138",
        )
        assert "InitMarginReq" not in values

    def test_unrequested_tag_filtered(self):
        summary = [
            make_account_value("BuyingPower", "200000.00", "CAD"),
        ]
        values, _ = _parse_summary_tags(
            summary, ["InitMarginReq"], "U18542138",
        )
        assert "BuyingPower" not in values

    def test_currency_reflects_last_seen_not_consistent(self):
        """Bug #3: currency return is whichever was processed last.

        If InitMarginReq comes from CAD and BuyingPower from USD, the
        returned currency says "USD" even though some values are CAD.
        The caller can't trust it as a uniform currency label.
        """
        summary = [
            make_account_value("InitMarginReq", "68000.00", "CAD"),
            make_account_value("BuyingPower", "200000.00", "USD"),
        ]
        values, currency = _parse_summary_tags(
            summary, ["InitMarginReq", "BuyingPower"], "U18542138",
        )
        # Currency reflects last-processed tag, not the dominant one
        assert currency == "USD"

    def test_base_currency_accepted(self):
        """BASE currency is accepted for non-Cushion tags."""
        summary = [
            make_account_value("InitMarginReq", "50000.00", "BASE"),
        ]
        values, _ = _parse_summary_tags(
            summary, ["InitMarginReq"], "U18542138",
        )
        assert values["InitMarginReq"] == 50000.0

    def test_empty_summary(self):
        values, currency = _parse_summary_tags([], ["InitMarginReq"], "U18542138")
        assert values == {}
        assert currency == "CAD"  # default

    def test_cad_overrides_usd_when_both_present(self):
        """If USD is seen first, CAD should still override it."""
        summary = [
            make_account_value("InitMarginReq", "50000.00", "USD"),
            make_account_value("InitMarginReq", "68000.00", "CAD"),
        ]
        values, _ = _parse_summary_tags(
            summary, ["InitMarginReq"], "U18542138",
        )
        assert values["InitMarginReq"] == 68000.0

    def test_usd_does_not_override_cad(self):
        """If CAD is seen first, USD should NOT replace it."""
        summary = [
            make_account_value("InitMarginReq", "68000.00", "CAD"),
            make_account_value("InitMarginReq", "50000.00", "USD"),
        ]
        values, _ = _parse_summary_tags(
            summary, ["InitMarginReq"], "U18542138",
        )
        assert values["InitMarginReq"] == 68000.0


# ── fetch_positions (mocked IB) ──────────────────────────


class TestFetchPositions:

    def _mock_ib(self, account_values, portfolio_items):
        ib = MagicMock()
        ib.accountValues.return_value = account_values
        ib.portfolio.return_value = portfolio_items
        return ib

    def test_single_stock(self):
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "100000.00", "CAD")],
            [make_portfolio_item("NVDA", position=100, market_value=15000.0,
                                 avg_cost=120.0, unrealized_pnl=3000.0)],
        )
        result = fetch_positions(ib, "U18542138")
        assert result["net_liquidation_value"] == 100000.0
        assert result["nlv_currency"] == "CAD"
        assert result["position_count"] == 1
        assert result["positions"][0]["symbol"] == "NVDA"
        assert result["positions"][0]["quantity"] == 100
        assert result["positions"][0]["allocation_pct"] == 100.0

    def test_multiple_sorted_by_allocation(self):
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "100000.00", "CAD")],
            [
                make_portfolio_item("SGOV", position=500, market_value=5000.0),
                make_portfolio_item("NVDA", position=100, market_value=15000.0),
                make_portfolio_item("TSM", position=200, market_value=10000.0),
            ],
        )
        result = fetch_positions(ib, "U18542138")
        symbols = [p["symbol"] for p in result["positions"]]
        assert symbols == ["NVDA", "TSM", "SGOV"]

    def test_allocations_sum_to_100(self):
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "100000.00", "CAD")],
            [
                make_portfolio_item("NVDA", position=100, market_value=15000.0),
                make_portfolio_item("TSM", position=200, market_value=10000.0),
                make_portfolio_item("SGOV", position=500, market_value=5000.0),
            ],
        )
        result = fetch_positions(ib, "U18542138")
        total = sum(p["allocation_pct"] for p in result["positions"])
        assert abs(total - 100.0) < 0.1  # within rounding tolerance

    def test_zero_qty_skipped(self):
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "100000.00", "CAD")],
            [
                make_portfolio_item("NVDA", position=100, market_value=15000.0),
                make_portfolio_item("CLOSED", position=0, market_value=0.0),
            ],
        )
        result = fetch_positions(ib, "U18542138")
        assert result["position_count"] == 1
        assert result["positions"][0]["symbol"] == "NVDA"

    def test_short_position_negative_qty(self):
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "100000.00", "CAD")],
            [make_portfolio_item("TSLA", position=-50, market_value=-7500.0,
                                 unrealized_pnl=500.0)],
        )
        result = fetch_positions(ib, "U18542138")
        pos = result["positions"][0]
        assert pos["quantity"] == -50
        assert pos["market_value"] == -7500.0
        # Gross exposure uses abs(market_value)
        assert result["gross_exposure"] == 7500.0

    def test_fractional_shares(self):
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "100000.00", "CAD")],
            [make_portfolio_item("NVDA", position=10.5, market_value=1575.0)],
        )
        result = fetch_positions(ib, "U18542138")
        assert result["positions"][0]["quantity"] == 10.5
        assert isinstance(result["positions"][0]["quantity"], float)

    def test_whole_shares_are_int(self):
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "100000.00", "CAD")],
            [make_portfolio_item("NVDA", position=100, market_value=15000.0)],
        )
        result = fetch_positions(ib, "U18542138")
        assert result["positions"][0]["quantity"] == 100
        assert isinstance(result["positions"][0]["quantity"], int)

    def test_cad_nlv_preferred_over_usd(self):
        ib = self._mock_ib(
            [
                make_account_value("NetLiquidation", "75000.00", "USD"),
                make_account_value("NetLiquidation", "100000.00", "CAD"),
            ],
            [make_portfolio_item("NVDA", position=100, market_value=15000.0)],
        )
        result = fetch_positions(ib, "U18542138")
        assert result["net_liquidation_value"] == 100000.0
        assert result["nlv_currency"] == "CAD"

    def test_usd_nlv_fallback(self):
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "75000.00", "USD")],
            [make_portfolio_item("NVDA", position=100, market_value=15000.0)],
        )
        result = fetch_positions(ib, "U18542138")
        assert result["net_liquidation_value"] == 75000.0
        assert result["nlv_currency"] == "USD"

    def test_nlv_not_found_raises(self):
        ib = self._mock_ib(
            [make_account_value("SomeOtherTag", "50000.00", "CAD")],
            [],
        )
        with pytest.raises(RuntimeError, match="NetLiquidation"):
            fetch_positions(ib, "U18542138")

    def test_leverage_calculation(self):
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "100000.00", "CAD")],
            [
                make_portfolio_item("NVDA", position=100, market_value=150000.0),
                make_portfolio_item("TSM", position=200, market_value=50000.0),
            ],
        )
        result = fetch_positions(ib, "U18542138")
        # gross_exposure = 150000 + 50000 = 200000, nlv = 100000
        assert result["leverage"] == 2.0

    def test_negative_nlv_returns_leverage_none(self):
        """Bug #4: Negative NLV → leverage=None, no alert generated.

        An underwater account silently gets no leverage calculation.
        Should arguably flag this as a critical alert.
        """
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "-5000.00", "CAD")],
            [make_portfolio_item("NVDA", position=100, market_value=15000.0)],
        )
        result = fetch_positions(ib, "U18542138")
        assert result["leverage"] is None

    def test_nan_unrealized_pnl_becomes_none(self):
        """IBKR returns NaN for unrealizedPNL when markets are closed.
        _q() now converts NaN → None for valid JSON output."""
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "100000.00", "CAD")],
            [make_portfolio_item("NVDA", position=100, market_value=15000.0,
                                 unrealized_pnl=float("nan"))],
        )
        result = fetch_positions(ib, "U18542138")
        assert result["positions"][0]["unrealized_pnl"] is None

    def test_unsubscribes_on_success(self):
        """reqAccountUpdates(False) is called to unsubscribe."""
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "100000.00", "CAD")],
            [make_portfolio_item("NVDA", position=100, market_value=15000.0)],
        )
        fetch_positions(ib, "U18542138")
        calls = ib.reqAccountUpdates.call_args_list
        assert calls[-1][0] == (False, "U18542138")

    def test_empty_portfolio(self):
        """Account with NLV but no positions."""
        ib = self._mock_ib(
            [make_account_value("NetLiquidation", "100000.00", "CAD")],
            [],
        )
        result = fetch_positions(ib, "U18542138")
        assert result["position_count"] == 0
        assert result["positions"] == []
        assert result["gross_exposure"] == 0.0


# ── fetch_open_orders ────────────────────────────────────


class TestFetchOpenOrders:

    def test_lmt_price_zero_preserved(self):
        """0.0 is a valid limit price (e.g., options). Must not become None."""
        ib = MagicMock()
        ib.openTrades.return_value = [make_trade(lmt_price=0.0)]
        result = fetch_open_orders(ib)
        assert result[0]["limit_price"] == 0.0

    def test_normal_limit_order(self):
        ib = MagicMock()
        ib.openTrades.return_value = [make_trade(lmt_price=150.0)]
        result = fetch_open_orders(ib)
        assert result[0]["limit_price"] == 150.0
        assert result[0]["order_type"] == "LMT"
        assert result[0]["symbol"] == "NVDA"

    def test_market_order(self):
        ib = MagicMock()
        ib.openTrades.return_value = [
            make_trade(order_type="MKT", lmt_price=0.0),
        ]
        result = fetch_open_orders(ib)
        assert result[0]["order_type"] == "MKT"

    def test_empty_list(self):
        ib = MagicMock()
        ib.openTrades.return_value = []
        result = fetch_open_orders(ib)
        assert result == []

    def test_multiple_orders(self):
        ib = MagicMock()
        ib.openTrades.return_value = [
            make_trade(symbol="NVDA", lmt_price=150.0),
            make_trade(symbol="TSM", action="SELL", lmt_price=200.0),
        ]
        result = fetch_open_orders(ib)
        assert len(result) == 2
        assert result[0]["symbol"] == "NVDA"
        assert result[1]["action"] == "SELL"


# ── fetch_pnl (mocked IB) ────────────────────────────────


class TestFetchPnl:
    """Tests for fetch_pnl and its internal _clean() NaN handler.

    _clean() is the ONLY place in the codebase that correctly converts
    NaN → None before JSON serialization. fetch_positions lacks this
    (Bug #1). If someone refactors _clean() out or breaks it, these
    tests catch the regression.
    """

    def _mock_ib(self, daily=100.0, unrealized=500.0, realized=0.0):
        ib = MagicMock()
        ib.reqPnL.return_value = SimpleNamespace(
            dailyPnL=daily, unrealizedPnL=unrealized, realizedPnL=realized,
        )
        return ib

    def test_normal_values(self):
        ib = self._mock_ib(daily=-1234.567, unrealized=5000.123, realized=200.999)
        result = fetch_pnl(ib, "U18542138")
        assert result == {
            "daily_pnl": -1234.57,
            "unrealized_pnl": 5000.12,
            "realized_pnl": 201.0,
        }

    def test_nan_converted_to_none(self):
        """_clean() converts NaN → None. This is the correct behavior
        that fetch_positions is missing (Bug #1)."""
        ib = self._mock_ib(
            daily=float("nan"), unrealized=float("nan"), realized=float("nan"),
        )
        result = fetch_pnl(ib, "U18542138")
        assert result["daily_pnl"] is None
        assert result["unrealized_pnl"] is None
        assert result["realized_pnl"] is None

    def test_none_converted_to_none(self):
        """None values (pre-market, no subscription data) pass through."""
        ib = self._mock_ib(daily=None, unrealized=None, realized=None)
        result = fetch_pnl(ib, "U18542138")
        assert result["daily_pnl"] is None
        assert result["unrealized_pnl"] is None
        assert result["realized_pnl"] is None

    def test_zero_preserved(self):
        """Zero is a valid P&L, not a sentinel — must not become None."""
        ib = self._mock_ib(daily=0.0, unrealized=0.0, realized=0.0)
        result = fetch_pnl(ib, "U18542138")
        assert result["daily_pnl"] == 0.0
        assert result["unrealized_pnl"] == 0.0
        assert result["realized_pnl"] == 0.0

    def test_mixed_nan_and_valid(self):
        """Realistic scenario: daily P&L available, unrealized NaN (closed)."""
        ib = self._mock_ib(daily=-500.0, unrealized=float("nan"), realized=0.0)
        result = fetch_pnl(ib, "U18542138")
        assert result["daily_pnl"] == -500.0
        assert result["unrealized_pnl"] is None
        assert result["realized_pnl"] == 0.0

    def test_cancels_subscription(self):
        """reqPnL subscription must be cancelled to avoid leaks."""
        ib = self._mock_ib()
        pnl_obj = ib.reqPnL.return_value
        fetch_pnl(ib, "U18542138")
        ib.cancelPnL.assert_called_once_with(pnl_obj)

    def test_rounds_to_two_decimals(self):
        ib = self._mock_ib(daily=1234.5678, unrealized=-99.999, realized=0.001)
        result = fetch_pnl(ib, "U18542138")
        assert result["daily_pnl"] == 1234.57
        assert result["unrealized_pnl"] == -100.0
        assert result["realized_pnl"] == 0.0


# ── _safe_fetch ──────────────────────────────────────────


class TestSafeFetch:

    def test_returns_result_on_success(self):
        result = _safe_fetch("test", lambda: {"key": "value"})
        assert result == {"key": "value"}

    def test_returns_none_on_exception(self):
        def failing():
            raise RuntimeError("API down")
        result = _safe_fetch("test", failing)
        assert result is None

    def test_passes_args_through(self):
        result = _safe_fetch("test", lambda x, y: x + y, 3, 4)
        assert result == 7

    def test_passes_kwargs_through(self):
        result = _safe_fetch("test", lambda x, key=1: x + key, 10, key=5)
        assert result == 15


# ── fetch_all ────────────────────────────────────────────


class TestFetchAll:

    def test_positions_failure_propagates(self):
        """fetch_positions is mandatory — its exception must propagate."""
        ib = MagicMock()
        ib.accountValues.return_value = []  # No NLV → RuntimeError
        ib.portfolio.return_value = []
        with pytest.raises(RuntimeError):
            fetch_all(ib, "U18542138")

    def test_optional_failures_return_none(self):
        """Margin/PNL/cash failures should not crash fetch_all."""
        ib = MagicMock()
        ib.accountValues.return_value = [
            make_account_value("NetLiquidation", "100000.00", "CAD"),
        ]
        ib.portfolio.return_value = [
            make_portfolio_item("NVDA", position=100, market_value=15000.0),
        ]
        # Make all optional fetchers fail
        ib.accountSummary.side_effect = RuntimeError("API down")
        ib.reqPnL.side_effect = RuntimeError("API down")
        ib.openTrades.side_effect = RuntimeError("API down")
        ib.reqExecutions.side_effect = RuntimeError("API down")

        result = fetch_all(ib, "U18542138")
        assert result["net_liquidation_value"] == 100000.0
        assert result["margin"] is None
        assert result["pnl"] is None
        assert result["cash"] is None
        assert result["open_orders"] is None
        assert result["recent_fills"] is None

    def test_structure_has_all_keys(self):
        ib = MagicMock()
        ib.accountValues.return_value = [
            make_account_value("NetLiquidation", "100000.00", "CAD"),
        ]
        ib.portfolio.return_value = [
            make_portfolio_item("NVDA", position=100, market_value=15000.0),
        ]
        ib.accountSummary.return_value = []
        ib.reqPnL.return_value = SimpleNamespace(
            dailyPnL=None, unrealizedPnL=None, realizedPnL=None,
        )
        ib.openTrades.return_value = []
        ib.reqExecutions.return_value = []
        ib.fills.return_value = []

        result = fetch_all(ib, "U18542138")
        expected_keys = {
            "account", "timestamp_utc", "net_liquidation_value", "nlv_currency",
            "gross_exposure", "leverage", "position_count", "positions",
            "margin", "pnl", "cash", "open_orders", "recent_fills",
            "alerts_triggered",
        }
        assert set(result.keys()) == expected_keys
