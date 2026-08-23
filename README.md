# NIFTY50 Signal Bot — TradingView → Telegram

Automated BUY/SELL alerts from a **UT Bot + Linear Regression Candles** indicator,
pulled from **TradingView's own data feed** and delivered to Telegram.

Runs on GitHub Actions. No PC required, no TradingView Pro, no broker subscription.

---

## Why TradingView as the data source

The indicator was built and validated on a TradingView chart. Any other data
provider introduces small OHLC discrepancies that shift signal timing.
`tvDatafeed` connects to TradingView's websocket and returns the *same bars that
draw your chart* — so signal parity is exact by construction, not by approximation.

Verified against the live chart on NSE:RELIANCE 30-min: 8/8 signals matched.

### Sources evaluated and rejected

| Source | Outcome |
|---|---|
| **NSEPython** | Dead. NSE geo-blocks non-Indian IPs (403), and its public API only serves *daily* candles — never 30-min. |
| **Zerodha enctoken** | Works, but the token expires every 24h with no automatable refresh. |
| **Kite Connect** | Requires a ₹2,000/month subscription. |
| **yfinance** | Works, but OHLC values drift from TradingView. Retained as a dormant fallback (`cloud_nifty_bot.py`). |

---

## How signals are evaluated

Signals are read from the **last _closed_ 30-minute bar**, never the bar currently
forming.

This matters. The forming bar's close keeps moving until the bar completes, so
evaluating it produces **repainting** — an alert appears, then the bar closes the
other way and the signal vanishes. Only closed bars are evaluated, so every alert
is final.

The closed bar is selected **by timestamp, not by position**. A bar labelled `T`
closes at `T + 30min`, so the newest bar satisfying `T + 30min <= now` is the one
to evaluate. Positional indexing (`-2`) silently picks the wrong bar whenever the
forming bar has not yet been emitted — which made alerts a full bar stale.

---

## Indicator parameters

These mirror the Pine Script exactly. Changing one here without changing it on
the chart will desynchronise the alerts.

| Parameter | Value |
|---|---|
| UT Bot — Key Value | `2` |
| UT Bot — ATR Period | `1` |
| LinReg — Signal Smoothing | `6` |
| LinReg — Simple MA (Signal Line) | `true` |
| LinReg — Linear Regression Length | `8` |

Confidence is `HIGH` when the LinReg candle colour agrees with the UT Bot
direction (BUY+green / SELL+red), otherwise `MEDIUM`.

---

## Timing

**Measured feed lag is ~2 seconds** (median 2.4s, max 5.8s across six bar
boundaries — see `measure_feed_lag.py`). The feed is effectively instant, so it
is not what delays an alert.

**GitHub's scheduler is the real constraint.** Scheduled workflows run on a
best-effort basis and are queued under load; starts can be many minutes late.

So rather than scheduling *at* bar close and hoping, the workflow fires **~3
minutes before** each close and the script waits out the remainder precisely:

```
cron: '12,42 4-9 * * 1-5'   # UTC → IST :12 and :42
```

- Job starts around IST `11:12`, sleeps until `11:15:25`, scans → alert ~30s
  after the bar closes.
- If GitHub starts the job late, the script detects that the boundary has
  already passed, skips the wait, and scans immediately.

12 runs per trading day ≈ **1,300 min/month** against GitHub's 2,000-minute free
tier. To reduce that, shift the cron to `13,43` for a shorter in-script wait.

> A severely delayed start may repeat the previous bar's alert. Every message
> carries its bar timestamp and how long ago that bar closed, so duplicates and
> staleness are always visible.

---

## Setup

### 1. Repository secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | From `@BotFather` |
| `TELEGRAM_CHAT_ID` | ✅ | From `@userinfobot` |
| `TV_USERNAME` | optional | TradingView login |
| `TV_PASSWORD` | optional | TradingView login |

The TradingView credentials are optional. Anonymous access works; supplying them
makes the feed use *your* account's data entitlement, matching your chart exactly.

### 2. First run

**Actions → NIFTY50 Signal Bot → Run workflow.** A Telegram message should arrive
within a minute — either signals, or a "No signals" confirmation.

---

## Files

| File | Purpose |
|---|---|
| `tv_signal_bot.py` | The bot — fetch, evaluate, alert |
| `tradingview_indicators.py` | UT Bot + LinReg implementations |
| `verify_tvdatafeed_quality.py` | Re-check data parity against your chart at any time |
| `requirements.txt` | Dependencies |
| `.github/workflows/tv-signal-bot.yml` | Schedule |
| `cloud_nifty_bot.py` | Dormant yfinance fallback — not wired to any workflow |

---

## Local run

```bash
pip install -r requirements.txt
# put TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env
python tv_signal_bot.py
```

To re-verify data parity against your chart:

```bash
python verify_tvdatafeed_quality.py
```

It prints recent timestamped signals — compare them directly against the
indicator on your TradingView chart.

---

## Editing the watchlist

`SYMBOLS` in `tv_signal_bot.py` uses **TradingView** symbol names, which
occasionally differ from other providers (e.g. `BAJAJ_AUTO`, not `BAJAJ-AUTO`).
If a symbol reports as unavailable, confirm its exact spelling on TradingView.

---

## Known limitations

- **NSE real-time vs delayed** depends on your TradingView data entitlement. The
  transport is live (verified: sub-minute lag on a 24/7 market), but NSE-specific
  freshness should be confirmed during a live session.
- The `15:15` stub bar (15:15–15:30) is not consistently emitted as a 30-min bar.
- Duplicate alerts are possible if GitHub's scheduler fires very late.
