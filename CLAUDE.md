# NIFTY50 Signal Bot

BUY/SELL alerts from a **UT Bot + Linear Regression Candles** indicator, sourced
from TradingView's own data feed and delivered to Telegram via GitHub Actions.
Runs entirely in the cloud — no local machine, no TradingView Pro, no broker fees.

Repo: `giriutage/nifty50-signal-bot` (private) · Deployed and verified green.

> An unrelated `CLAUDE.md` for a wealth-dashboard project lives at
> `e:\CLAUDECODE\.claude\CLAUDE.md` and also loads in this directory. It does not
> apply here.

## Pipeline

```
GitHub Actions (cron) → tvDatafeed (TradingView websocket)
    → TradingViewIndicators → Telegram Bot API → phone
```

| File | Role |
|---|---|
| `tv_signal_bot.py` | The bot: fetch → evaluate → alert |
| `tradingview_indicators.py` | `ut_bot_alert()`, `linear_reg_candles()` |
| `.github/workflows/tv-signal-bot.yml` | Schedule + secrets wiring |
| `verify_tvdatafeed_quality.py` | Re-check data parity against the chart |
| `measure_feed_lag.py` | Measure bar-close → availability lag |
| `cloud_nifty_bot.py` | **Dormant** yfinance fallback ("Option Z"). Not wired to any workflow. |

## Invariants — do not break these

**1. Indicator parameters must mirror the Pine Script exactly.**
`key_value=2`, `atr_period=1`, `signal_length=6`, `use_sma=True`, `linreg_length=8`.
Changing one here without changing it on the chart desynchronises every alert.

**2. Only ever evaluate a CLOSED bar.** The forming bar's close keeps moving, so
evaluating it causes repainting — alerts that fire then vanish. The user
explicitly chose confirmed-only alerts over early/provisional ones.

**3. Select the closed bar by TIMESTAMP, never by position.** A bar labelled `T`
closes at `T + 30min`; take the newest bar where `T + 30min <= now`
(`last_closed_index()`). Positional `-2` was a real bug — it skipped a valid
closed bar whenever the forming bar had not yet been emitted, making every alert
one full 30-minute bar stale.

**4. Never commit `.env`.** It holds the Telegram token. It is gitignored;
verify with `git ls-tree -r --name-only origin/main` after any push.

## Data source: why tvDatafeed

The indicator was built and validated on a TradingView chart, so any other
provider introduces OHLC drift that shifts signal timing. tvDatafeed reads
TradingView's own websocket — parity is exact by construction. Verified 8/8
signals against the live chart on NSE:RELIANCE 30-min.

**Alternatives already investigated and rejected — do not re-explore:**

| Source | Why rejected |
|---|---|
| **NSEPython** | Two fatal blockers: NSE geo-blocks non-Indian IPs (returns HTTP 403 + an HTML page, which surfaces as `KeyError: 'data'`), and its public API serves **daily candles only** — never 30-min. |
| **Zerodha enctoken** (`pyzdata`) | Works, but the token expires every 24h with no refresh path that survives a headless runner. |
| **Kite Connect** | Requires a ₹2,000/month subscription for any historical access. |
| **yfinance** | Works, but OHLC values drift from TradingView. Kept dormant as last resort. |

## Timing design

Measured feed lag (bar close → bar available) is **~2 seconds** (median 2.4s,
max 5.8s). The feed is not the constraint.

**GitHub's scheduler is.** Scheduled workflows are best-effort and get queued
under load, starting minutes late. So the workflow fires ~3 minutes *before* each
bar close and `wait_for_bar_close()` sleeps until 25s past the boundary. If the
job starts late anyway, the wait is skipped and it scans immediately.

```
cron: '12,42 4-9 * * 1-5'    # UTC → IST :12 and :42
```

NSE 30-min bars close at `:15` and `:45` IST. 12 runs/trading day ≈ 1,300 of the
2,000 free minutes. Shift cron to `13,43` to trim it. **Making the repo public
grants unlimited Actions minutes** — nothing sensitive is in the code.

Best-case latency: alert lands ~30s after the candle closes.

## Gotchas

- **`git` is not on PATH.** Use `& "C:\Program Files\Git\bin\git.exe"`.
- **`tvdatafeed` installs from GitHub, not PyPI**:
  `git+https://github.com/rongardF/tvdatafeed.git`. Requires `git` on the runner
  (ubuntu-latest has it).
- **tvDatafeed returns naive timestamps in the machine's local timezone** — UTC
  on Actions, CEST locally. Localise with `LOCAL_TZ` then convert to IST. This
  affects displayed times only; indicator math depends on bar order alone.
- **TradingView symbol names differ from other providers** — `BAJAJ_AUTO`, not
  `BAJAJ-AUTO`. If a symbol reports unavailable, check its spelling on
  TradingView. `fetch()` retries transient empty responses.
- **The `15:15` stub bar** (15:15–15:30) is not consistently emitted as a 30-min bar.
- **Telegram sends retry with backoff** — a transient timeout must never drop a
  signal. Observed in testing.
- Python here is `pythoncore-3.14` (system), not a venv.

## GitHub Actions secrets

| Secret | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | From `@BotFather` |
| `TELEGRAM_CHAT_ID` | yes | From `@userinfobot` |
| `TV_USERNAME` / `TV_PASSWORD` | no | Use the user's TradingView data entitlement instead of anonymous |

Anonymous TradingView access is **confirmed working from GitHub's US datacenter
IPs**. The credentials are only needed if NSE data proves delayed.

## Open item

**NSE real-time vs delayed** is unconfirmed — it depends on TradingView account
entitlement, not on code. Every Telegram message prints `bar closed X min ago`:
~0.5 min means real-time; ~15 min means delayed, and the fix is adding
`TV_USERNAME` / `TV_PASSWORD`. Check during a live session (09:15–15:30 IST).

## Verification

```bash
python tv_signal_bot.py            # full run, sends to Telegram
python verify_tvdatafeed_quality.py # print signals to compare against the chart
python measure_feed_lag.py          # re-measure feed lag
```

Data parity is checked by comparing `verify_tvdatafeed_quality.py` output
against the indicator on the user's TradingView chart — that is the ground truth.
