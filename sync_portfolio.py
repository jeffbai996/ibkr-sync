"""
Entry point for IBKR portfolio sync. Connects to TWS, pulls all
portfolio data, writes ~/.claude/portfolio_state.json.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from ib_insync import IB

from alerts import check_alerts, fire_webhook
from fetchers import fetch_all

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

OUTPUT_PATH = Path.home() / ".claude" / "portfolio_state.json"


def connect_ib(max_retries: int = 3) -> IB:
    """Connect to TWS/Gateway with exponential backoff."""
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "7496"))
    client_id = int(os.getenv("IBKR_CLIENT_ID", "10"))

    ib = IB()
    for attempt in range(1, max_retries + 1):
        try:
            ib.connect(host, port, clientId=client_id, timeout=10)
            log.info("Connected to IBKR — %s", ib.managedAccounts())
            return ib
        except Exception as e:
            if attempt == max_retries:
                log.error("Failed to connect after %d attempts — %s", max_retries, e)
                sys.exit(1)
            delay = 2 ** attempt
            log.warning("Connect attempt %d failed, retrying in %ds — %s", attempt, delay, e)
            time.sleep(delay)

    # Unreachable, but satisfies type checker
    sys.exit(1)


def load_alert_config() -> dict:
    """Load alert thresholds from environment."""
    config = {}
    for key in ("ALERT_LEVERAGE_MAX", "ALERT_CUSHION_MIN", "ALERT_DAILY_PNL_THRESHOLD"):
        raw = os.getenv(key)
        if not raw:
            continue
        try:
            config[key] = float(raw)
        except ValueError:
            log.warning("Ignoring invalid %s value: %s", key, raw)
    return config


def write_output(data: dict) -> None:
    """Write portfolio state JSON to Claude config directory."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, indent=2) + "\n")
    log.info("Wrote portfolio state to %s", OUTPUT_PATH)


def print_summary(data: dict) -> None:
    """Print compact portfolio summary to stdout."""
    print(f"\n{'─' * 55}")
    print(f"Account: {data['account']}")
    print(f"NLV: ${data['net_liquidation_value']:,.2f} {data['nlv_currency']}")
    print(f"Gross: ${data['gross_exposure']:,.2f}")
    print(f"Leverage: {data['leverage']}x")
    print(f"Positions: {data['position_count']}")

    # Margin info
    margin = data.get("margin")
    if margin:
        cushion = margin.get("cushion")
        if cushion is not None:
            print(f"Cushion: {cushion:.4f}")
        excess = margin.get("excess_liquidity")
        if excess is not None:
            print(f"Excess Liq: ${excess:,.2f}")

    # Daily P&L
    pnl = data.get("pnl")
    if pnl and pnl.get("daily_pnl") is not None:
        daily = pnl["daily_pnl"]
        sign = "+" if daily >= 0 else ""
        print(f"Daily P&L: {sign}${daily:,.2f}")

    print(f"{'─' * 55}")
    for p in data["positions"][:10]:
        print(f"  {p['symbol']:>6s}  {p['allocation_pct']:5.1f}%  ${p['market_value']:>12,.2f}")
    if data["position_count"] > 10:
        print(f"  ... and {data['position_count'] - 10} more")

    # Alerts
    alerts = data.get("alerts_triggered", [])
    if alerts:
        print(f"{'─' * 55}")
        for a in alerts:
            print(f"  ⚠ {a['message']}")

    print(f"{'─' * 55}\n")


def run_once(ib: IB, account: str) -> None:
    """Single sync cycle: fetch → alerts → write → print."""
    data = fetch_all(ib, account)
    data["timestamp_utc"] = datetime.now(timezone.utc).isoformat()

    alert_config = load_alert_config()
    alerts = check_alerts(data, alert_config)
    data["alerts_triggered"] = alerts

    write_output(data)
    print_summary(data)

    fire_webhook(os.getenv("ALERT_WEBHOOK_URL"), alerts, data)


def run_loop(ib: IB, account: str, interval: int) -> None:
    """Continuous sync with reconnection on disconnect."""
    log.info("Starting loop mode, interval=%ds (Ctrl+C to stop)", interval)
    while True:
        try:
            if not ib.isConnected():
                log.warning("Disconnected, reconnecting...")
                ib = connect_ib()
            run_once(ib, account)
        except KeyboardInterrupt:
            raise
        except Exception:
            log.exception("Cycle failed, will retry next interval")
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync IBKR portfolio state")
    parser.add_argument("--mode", choices=["once", "loop"], default="once",
                        help="Run once or loop continuously (default: once)")
    parser.add_argument("--interval", type=int,
                        default=int(os.getenv("SYNC_INTERVAL", "300")),
                        help="Seconds between syncs in loop mode (default: 300)")
    args = parser.parse_args()

    account = os.getenv("IBKR_ACCOUNT")
    if not account:
        log.error("IBKR_ACCOUNT env var not set")
        sys.exit(1)
    ib = connect_ib()

    try:
        if args.mode == "loop":
            run_loop(ib, account, args.interval)
        else:
            run_once(ib, account)
    except KeyboardInterrupt:
        log.info("Interrupted, shutting down")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
