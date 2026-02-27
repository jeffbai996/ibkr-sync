"""
Tests for sync_portfolio.py — config loading, output writing, summary printing.

These test the I/O boundary: reading env vars, writing JSON files, and
formatting console output. All use monkeypatch/tmp_path/capsys to avoid
touching real filesystems or environment.
"""

import json

import pytest

from sync_portfolio import load_alert_config, write_output, print_summary


# ── load_alert_config ────────────────────────────────────


class TestLoadAlertConfig:

    def test_all_set(self, monkeypatch):
        monkeypatch.setenv("ALERT_LEVERAGE_MAX", "3.5")
        monkeypatch.setenv("ALERT_CUSHION_MIN", "0.10")
        monkeypatch.setenv("ALERT_DAILY_PNL_THRESHOLD", "1000")
        config = load_alert_config()
        assert config == {
            "ALERT_LEVERAGE_MAX": 3.5,
            "ALERT_CUSHION_MIN": 0.10,
            "ALERT_DAILY_PNL_THRESHOLD": 1000.0,
        }

    def test_none_set(self, monkeypatch):
        monkeypatch.delenv("ALERT_LEVERAGE_MAX", raising=False)
        monkeypatch.delenv("ALERT_CUSHION_MIN", raising=False)
        monkeypatch.delenv("ALERT_DAILY_PNL_THRESHOLD", raising=False)
        config = load_alert_config()
        assert config == {}

    def test_partial_set(self, monkeypatch):
        monkeypatch.delenv("ALERT_LEVERAGE_MAX", raising=False)
        monkeypatch.setenv("ALERT_CUSHION_MIN", "0.15")
        monkeypatch.delenv("ALERT_DAILY_PNL_THRESHOLD", raising=False)
        config = load_alert_config()
        assert config == {"ALERT_CUSHION_MIN": 0.15}

    def test_invalid_float_skipped(self, monkeypatch):
        """Non-numeric env var is logged and skipped, not crash."""
        monkeypatch.setenv("ALERT_LEVERAGE_MAX", "abc")
        monkeypatch.delenv("ALERT_CUSHION_MIN", raising=False)
        monkeypatch.delenv("ALERT_DAILY_PNL_THRESHOLD", raising=False)
        config = load_alert_config()
        assert "ALERT_LEVERAGE_MAX" not in config

    def test_empty_string_skipped(self, monkeypatch):
        """Empty env var is falsy, so `if os.getenv(...)` skips it."""
        monkeypatch.setenv("ALERT_LEVERAGE_MAX", "")
        monkeypatch.delenv("ALERT_CUSHION_MIN", raising=False)
        monkeypatch.delenv("ALERT_DAILY_PNL_THRESHOLD", raising=False)
        config = load_alert_config()
        assert "ALERT_LEVERAGE_MAX" not in config


# ── write_output ─────────────────────────────────────────


class TestWriteOutput:

    def test_valid_json_roundtrip(self, tmp_path, monkeypatch):
        output_file = tmp_path / "portfolio_state.json"
        monkeypatch.setattr("sync_portfolio.OUTPUT_PATH", output_file)
        data = {"account": "U18542138", "net_liquidation_value": 100000.0}
        write_output(data)
        loaded = json.loads(output_file.read_text())
        assert loaded == data

    def test_creates_parent_dirs(self, tmp_path, monkeypatch):
        output_file = tmp_path / "nested" / "deep" / "portfolio_state.json"
        monkeypatch.setattr("sync_portfolio.OUTPUT_PATH", output_file)
        write_output({"test": True})
        assert output_file.exists()

    def test_overwrites_existing(self, tmp_path, monkeypatch):
        output_file = tmp_path / "portfolio_state.json"
        monkeypatch.setattr("sync_portfolio.OUTPUT_PATH", output_file)
        write_output({"version": 1})
        write_output({"version": 2})
        loaded = json.loads(output_file.read_text())
        assert loaded == {"version": 2}


# ── print_summary ────────────────────────────────────────


def _full_data(**overrides):
    """Build minimal valid data dict for print_summary."""
    data = {
        "account": "U18542138",
        "net_liquidation_value": 100000.0,
        "nlv_currency": "CAD",
        "gross_exposure": 150000.0,
        "leverage": 1.5,
        "position_count": 2,
        "positions": [
            {"symbol": "NVDA", "allocation_pct": 60.0, "market_value": 90000.0},
            {"symbol": "TSM", "allocation_pct": 40.0, "market_value": 60000.0},
        ],
        "margin": {"cushion": 0.25, "excess_liquidity": 50000.0},
        "pnl": {"daily_pnl": 1234.56},
        "alerts_triggered": [],
    }
    data.update(overrides)
    return data


class TestPrintSummary:

    def test_no_crash_on_minimal_data(self, capsys):
        print_summary(_full_data())
        output = capsys.readouterr().out
        assert "U18542138" in output
        assert "NVDA" in output

    def test_handles_none_margin(self, capsys):
        print_summary(_full_data(margin=None))
        output = capsys.readouterr().out
        assert "Cushion" not in output

    def test_handles_none_pnl(self, capsys):
        print_summary(_full_data(pnl=None))
        output = capsys.readouterr().out
        assert "Daily P&L" not in output

    def test_handles_pnl_with_none_daily(self, capsys):
        print_summary(_full_data(pnl={"daily_pnl": None}))
        output = capsys.readouterr().out
        assert "Daily P&L" not in output

    def test_truncates_at_10_positions(self, capsys):
        positions = [
            {"symbol": f"SYM{i}", "allocation_pct": 8.0, "market_value": 10000.0}
            for i in range(15)
        ]
        print_summary(_full_data(positions=positions, position_count=15))
        output = capsys.readouterr().out
        assert "... and 5 more" in output

    def test_shows_alerts(self, capsys):
        alerts = [{
            "type": "leverage_high",
            "message": "Leverage 3.50x exceeds max 3.00x",
        }]
        print_summary(_full_data(alerts_triggered=alerts))
        output = capsys.readouterr().out
        assert "Leverage 3.50x" in output

    def test_positive_pnl_has_plus_sign(self, capsys):
        print_summary(_full_data(pnl={"daily_pnl": 500.0}))
        output = capsys.readouterr().out
        assert "+$500.00" in output

    def test_negative_pnl_format(self, capsys):
        print_summary(_full_data(pnl={"daily_pnl": -500.0}))
        output = capsys.readouterr().out
        # Format is: sign + "$" + f-string, where sign="" for negative
        # so output contains "$-500.00"
        assert "$-500.00" in output
