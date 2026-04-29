# ibkr-sync

> **DEPRECATED — superseded by [ibkr-terminal](https://github.com/jeffbai996/ibkr-terminal).**
> This repository is archived and no longer maintained. Use ibkr-terminal for all portfolio/IBKR integration. The code below is preserved for historical reference only.

Pulls portfolio state from IBKR TWS/Gateway and writes `~/.claude/portfolio_state.json` so Claude Code sessions have live position data. Optionally fires webhook alerts when leverage, cushion, or daily P&L breach thresholds.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your values. Defaults work if TWS is running locally on the standard port.

## Usage

```bash
# One-shot sync
python sync_portfolio.py

# Continuous (reconnects on disconnect)
python sync_portfolio.py --mode loop

# Custom interval
python sync_portfolio.py --mode loop --interval 60
```

TWS or IB Gateway must be running with API connections enabled (Edit > Global Config > API > Settings).

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `IBKR_HOST` | `127.0.0.1` | |
| `IBKR_PORT` | `7496` | 7496=TWS live, 7497=TWS paper, 4001/4002=Gateway |
| `IBKR_CLIENT_ID` | `10` | Must be unique per concurrent connection |
| `IBKR_ACCOUNT` | `U18542138` | |
| `SYNC_INTERVAL` | `300` | Seconds between syncs in loop mode |

### Alert Thresholds (all optional)

| Variable | Triggers when |
|---|---|
| `ALERT_LEVERAGE_MAX` | Leverage exceeds value |
| `ALERT_CUSHION_MIN` | Margin cushion drops below value |
| `ALERT_DAILY_PNL_THRESHOLD` | Daily P&L exceeds +/- value |
| `ALERT_WEBHOOK_URL` | POST destination for triggered alerts |

## Scheduling via launchd

```bash
cp com.jeffbai.ibkr-sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jeffbai.ibkr-sync.plist
```

Runs `--mode once` every 300s. Logs to `/tmp/ibkr-sync.log` and `/tmp/ibkr-sync.err`.

```bash
# Check if it's running
launchctl list | grep ibkr

# Unload
launchctl unload ~/Library/LaunchAgents/com.jeffbai.ibkr-sync.plist
```

## Architecture

```
sync_portfolio.py    Entry point, CLI, orchestration
fetchers.py          IBKR data extraction (positions, margin, P&L, cash, orders, fills)
alerts.py            Threshold checks + webhook POST
```

Positions are mandatory — sync fails if they can't be fetched. Everything else is best-effort (returns `null` on failure).

## Testing

```bash
python -m pytest tests/ -v
```

97 tests covering alerts, position math, P&L handling, config loading, and JSON output. No TWS connection needed — all IBKR objects are mocked.

Bug-documenting tests are tagged with pytest markers (`bug1_nan_crash`, `bug2_lmt_price_zero`, etc.) so they can be run in isolation:

```bash
python -m pytest tests/ -m bug1_nan_crash -v
```

## Output

Writes to `~/.claude/portfolio_state.json`. Key fields:

- `net_liquidation_value`, `nlv_currency` — account NLV (prefers CAD)
- `gross_exposure`, `leverage` — total absolute market value and leverage ratio
- `positions[]` — sorted by allocation, each with symbol, qty, avg cost, market value, unrealized P&L, allocation %
- `margin` — cushion, excess liquidity, buying power, SMA, init/maint margin req
- `pnl` — daily, unrealized, realized (null when market closed)
- `cash` — total, settled, accrued, per-currency breakdown
- `open_orders[]`, `recent_fills[]` — today's activity
- `alerts_triggered[]` — any breached thresholds
