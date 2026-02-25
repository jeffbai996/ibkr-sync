"""
Tests for alerts.py: check_alerts() and fire_webhook().

check_alerts() is pure — takes data dict + config dict, returns list of
alert dicts. No side effects, no network calls. This makes it the highest
value test target: it guards margin safety and is trivially testable.

fire_webhook() does I/O (HTTP POST) so we mock urllib.
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from alerts import check_alerts, fire_webhook


# ── Minimal data builder ─────────────────────────────────


def _data(leverage=1.5, cushion=0.20, daily_pnl=-500.0):
    """Build minimal portfolio data dict for alert testing."""
    d = {
        "account": "U18542138",
        "timestamp_utc": "2026-02-27T12:00:00+00:00",
        "net_liquidation_value": 100000.0,
        "leverage": leverage,
    }
    if cushion is not None:
        d["margin"] = {"cushion": cushion}
    else:
        d["margin"] = None
    if daily_pnl is not None:
        d["pnl"] = {"daily_pnl": daily_pnl}
    else:
        d["pnl"] = None
    return d


# ── Leverage alerts ──────────────────────────────────────


class TestLeverageAlert:

    def test_fires_above_threshold(self):
        alerts = check_alerts(_data(leverage=3.5), {"ALERT_LEVERAGE_MAX": 3.0})
        assert len(alerts) == 1
        assert alerts[0]["type"] == "leverage_high"
        assert alerts[0]["value"] == 3.5
        assert alerts[0]["threshold"] == 3.0

    def test_silent_below_threshold(self):
        alerts = check_alerts(_data(leverage=2.0), {"ALERT_LEVERAGE_MAX": 3.0})
        assert len(alerts) == 0

    def test_exact_threshold_no_alert(self):
        """Operator is > not >=, so exact match doesn't fire."""
        alerts = check_alerts(_data(leverage=3.0), {"ALERT_LEVERAGE_MAX": 3.0})
        assert len(alerts) == 0

    def test_leverage_none_skipped(self):
        alerts = check_alerts(_data(leverage=None), {"ALERT_LEVERAGE_MAX": 3.0})
        assert len(alerts) == 0

    def test_config_missing_leverage_skipped(self):
        alerts = check_alerts(_data(leverage=5.0), {})
        assert len(alerts) == 0


# ── Cushion alerts ───────────────────────────────────────


class TestCushionAlert:

    def test_fires_below_threshold(self):
        alerts = check_alerts(_data(cushion=0.05), {"ALERT_CUSHION_MIN": 0.10})
        assert len(alerts) == 1
        assert alerts[0]["type"] == "cushion_low"

    def test_silent_above_threshold(self):
        alerts = check_alerts(_data(cushion=0.30), {"ALERT_CUSHION_MIN": 0.10})
        assert len(alerts) == 0

    def test_margin_none_skipped(self):
        alerts = check_alerts(_data(cushion=None), {"ALERT_CUSHION_MIN": 0.10})
        assert len(alerts) == 0

    def test_margin_missing_cushion_key(self):
        data = _data()
        data["margin"] = {"init_margin_req": 50000}  # no cushion key
        alerts = check_alerts(data, {"ALERT_CUSHION_MIN": 0.10})
        assert len(alerts) == 0


# ── Daily P&L alerts ─────────────────────────────────────


class TestDailyPnlAlert:

    def test_loss_fires(self):
        alerts = check_alerts(
            _data(daily_pnl=-1500.0), {"ALERT_DAILY_PNL_THRESHOLD": 1000.0},
        )
        assert len(alerts) == 1
        assert alerts[0]["type"] == "daily_pnl_loss"

    def test_gain_fires(self):
        alerts = check_alerts(
            _data(daily_pnl=2000.0), {"ALERT_DAILY_PNL_THRESHOLD": 1000.0},
        )
        assert len(alerts) == 1
        assert alerts[0]["type"] == "daily_pnl_gain"

    def test_within_threshold_silent(self):
        alerts = check_alerts(
            _data(daily_pnl=-500.0), {"ALERT_DAILY_PNL_THRESHOLD": 1000.0},
        )
        assert len(alerts) == 0

    def test_pnl_none_skipped(self):
        alerts = check_alerts(
            _data(daily_pnl=None), {"ALERT_DAILY_PNL_THRESHOLD": 1000.0},
        )
        assert len(alerts) == 0

    def test_pnl_dict_none_skipped(self):
        data = _data()
        data["pnl"] = None
        alerts = check_alerts(data, {"ALERT_DAILY_PNL_THRESHOLD": 1000.0})
        assert len(alerts) == 0


# ── Combined alerts ──────────────────────────────────────


class TestCombinedAlerts:

    def test_multiple_alerts_fire_simultaneously(self):
        config = {
            "ALERT_LEVERAGE_MAX": 2.0,
            "ALERT_CUSHION_MIN": 0.15,
            "ALERT_DAILY_PNL_THRESHOLD": 500.0,
        }
        data = _data(leverage=3.0, cushion=0.05, daily_pnl=-1000.0)
        alerts = check_alerts(data, config)
        types = {a["type"] for a in alerts}
        assert types == {"leverage_high", "cushion_low", "daily_pnl_loss"}

    def test_empty_config_produces_zero_alerts(self):
        alerts = check_alerts(
            _data(leverage=10.0, cushion=0.001, daily_pnl=-99999), {},
        )
        assert alerts == []


# ── fire_webhook ─────────────────────────────────────────


class TestFireWebhook:

    def test_noop_on_none_url(self):
        """Should not raise or make any network call."""
        fire_webhook(None, [{"type": "test"}], _data())

    def test_noop_on_empty_alerts(self):
        fire_webhook("https://example.com/hook", [], _data())

    def test_noop_on_empty_string_url(self):
        fire_webhook("", [{"type": "test"}], _data())

    @patch("alerts.urllib.request.urlopen")
    def test_payload_structure(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        alert = [{
            "type": "leverage_high", "message": "test",
            "value": 3.5, "threshold": 3.0,
        }]
        data = _data(leverage=3.5)
        fire_webhook("https://example.com/hook", alert, data)

        mock_urlopen.assert_called_once()
        request = mock_urlopen.call_args[0][0]
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["account"] == "U18542138"
        assert payload["alerts"] == alert
        assert payload["leverage"] == 3.5
        assert payload["nlv"] == 100000.0
        assert request.get_header("Content-type") == "application/json"

    @patch("alerts.urllib.request.urlopen", side_effect=Exception("Connection refused"))
    def test_network_failure_logged_not_raised(self, mock_urlopen, caplog):
        """Network failure should be logged, not propagated."""
        with caplog.at_level(logging.ERROR):
            fire_webhook("https://example.com/hook", [{"type": "test"}], _data())
        assert "failed" in caplog.text.lower()
