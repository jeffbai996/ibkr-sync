"""
Threshold checks and webhook notifications for portfolio alerts.
"""

import json
import logging
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)


def check_alerts(data: dict, config: dict) -> list:
    """Check portfolio data against alert thresholds.

    config keys (all optional, alert skipped if missing):
        ALERT_LEVERAGE_MAX: float
        ALERT_CUSHION_MIN: float
        ALERT_DAILY_PNL_THRESHOLD: float (absolute value, checks both directions)
    """
    alerts = []

    leverage = data.get("leverage")
    max_lev = config.get("ALERT_LEVERAGE_MAX")
    if leverage is not None and max_lev is not None and leverage > max_lev:
        alerts.append({
            "type": "leverage_high",
            "message": f"Leverage {leverage:.2f}x exceeds max {max_lev:.2f}x",
            "value": leverage,
            "threshold": max_lev,
        })

    cushion = data.get("margin", {}).get("cushion") if data.get("margin") else None
    min_cushion = config.get("ALERT_CUSHION_MIN")
    if cushion is not None and min_cushion is not None and cushion < min_cushion:
        alerts.append({
            "type": "cushion_low",
            "message": f"Margin cushion {cushion:.4f} below min {min_cushion:.4f}",
            "value": cushion,
            "threshold": min_cushion,
        })

    daily_pnl = data.get("pnl", {}).get("daily_pnl") if data.get("pnl") else None
    pnl_thresh = config.get("ALERT_DAILY_PNL_THRESHOLD")
    if daily_pnl is not None and pnl_thresh is not None:
        if daily_pnl < -pnl_thresh:
            alerts.append({
                "type": "daily_pnl_loss",
                "message": f"Daily P&L ${daily_pnl:,.2f} exceeds loss threshold ${-pnl_thresh:,.2f}",
                "value": daily_pnl,
                "threshold": -pnl_thresh,
            })
        elif daily_pnl > pnl_thresh:
            alerts.append({
                "type": "daily_pnl_gain",
                "message": f"Daily P&L ${daily_pnl:,.2f} exceeds gain threshold ${pnl_thresh:,.2f}",
                "value": daily_pnl,
                "threshold": pnl_thresh,
            })

    return alerts


def fire_webhook(url: Optional[str], alerts: list, data: dict) -> None:
    """POST triggered alerts to webhook URL. No-op if URL is empty."""
    if not url or not alerts:
        return

    payload = json.dumps({
        "account": data.get("account"),
        "timestamp_utc": data.get("timestamp_utc"),
        "alerts": alerts,
        "leverage": data.get("leverage"),
        "nlv": data.get("net_liquidation_value"),
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("Webhook fired (%d): %d alert(s)", resp.status, len(alerts))
    except Exception:
        log.exception("Webhook POST to %s failed", url)
