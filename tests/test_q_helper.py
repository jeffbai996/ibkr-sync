"""
Tests for the _q() quantization helper in fetchers.py.

_q() converts values to Decimal, quantizes to 2 decimal places (banker's
rounding / ROUND_HALF_EVEN), and returns float. It's used on every financial
value before JSON serialization.
"""

from decimal import Decimal, InvalidOperation

import pytest

from fetchers import _q


class TestQNormal:
    """Standard rounding behavior."""

    def test_rounds_to_two_decimals(self):
        assert _q(150.456) == 150.46

    def test_integer_input(self):
        assert _q(100) == 100.0

    def test_negative_value(self):
        assert _q(-42.999) == -43.0

    def test_zero(self):
        assert _q(0) == 0.0

    def test_very_small_value(self):
        assert _q(0.001) == 0.0

    def test_very_large_value(self):
        assert _q(1_000_000_000.999) == 1_000_000_001.0

    def test_already_two_decimals(self):
        assert _q(42.50) == 42.50

    def test_returns_float(self):
        result = _q(100)
        assert isinstance(result, float)

    def test_decimal_input(self):
        assert _q(Decimal("99.995")) == 100.0

    def test_string_numeric(self):
        """_q converts via str() then Decimal, so string numbers work."""
        assert _q("42.567") == 42.57


class TestQBankersRounding:
    """Decimal uses ROUND_HALF_EVEN by default (banker's rounding).

    When the digit to be dropped is exactly 5, it rounds to the nearest
    even number. This avoids systematic upward bias in financial sums.
    """

    def test_half_rounds_to_even_down(self):
        # 0.125 → 0.12 (2 is even, so round down)
        assert _q(0.125) == 0.12

    def test_half_rounds_to_even_up(self):
        # 0.135 → 0.14 (3 is odd, so round up to 4)
        assert _q(0.135) == 0.14


class TestQEdgeCases:
    """_q() handles NaN, None, and Inf from IBKR gracefully."""

    def test_nan_returns_none(self):
        assert _q(float("nan")) is None

    def test_none_returns_none(self):
        assert _q(None) is None

    def test_inf_raises(self):
        """Inf is not a valid financial value — still raises."""
        with pytest.raises(InvalidOperation):
            _q(float("inf"))
