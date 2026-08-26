# NIFTY50 Signal Bot

> **Status: the bot works; the strategy does not.**
>
> The delivery pipeline is live and verified — alerts land ~30s after each
> candle closes. But extensive backtesting (see *Research findings* below)
> shows these signals carry **no tradeable edge after costs**. Buy-and-hold
> NIFTY beat the best variant on both return and drawdown.
>
> Treat the alerts as an indicator feed, not a trade recommendation. The user
> paused here in Aug 2026 to look for a different strategy. Do not resume
> parameter tuning on this one — that ground is covered and exhausted.

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
| `.github/workflows/session-morning.yml` | Phase A schedule (09:45–12:15 IST) |
| `.github/workflows/session-afternoon.yml` | Phase B schedule (12:45–15:15 IST) |
| `.github/workflows/manual.yml` | `workflow_dispatch` + `repository_dispatch`, no phase limit |
| `verify_tvdatafeed_quality.py` | Re-check data parity against the chart |
| `measure_feed_lag.py` | Measure bar-close → availability lag |
| `cloud_nifty_bot.py` | **Dormant** yfinance fallback ("Option Z"). Not wired to any workflow. |

## Invariants — do not break these

**1. Indicator parameters must mirror the Pine Script exactly.**
`key_value=2`, `atr_period=1`, `signal_length=7`, `use_sma=True`, `linreg_length=11`.
Changing one here without changing it on the chart desynchronises every alert.

> Note which parameters actually move signals. In the Pine Script,
> `buy`/`sell` derive solely from `src` and `xATRTrailingStop` — that is,
> **Key Value and ATR Period alone**. The LinReg values feed `plotcandle` and
> the signal line only. So `signal_length` / `linreg_length` change nothing
> about *which* signals fire; they only recolour the candles, which the bot
> reads as the HIGH/MEDIUM confidence label. Verified empirically: 6/8 and
> 7/11 both produced 49 BUY + 49 SELL over the same 1,000 bars, differing
> only in confidence split (93% vs 84% HIGH).

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

## Timing design — one long job, not 12 scheduled runs

Measured feed lag (bar close → bar available) is **~2 seconds**. The feed is
never the constraint.

**GitHub's cron is, and this was proven in production — do not go back to it.**
Running 12 scheduled jobs a day dropped roughly **half** of them (a full session
alerted only on `:45` bars, never `:15`) and started the survivors **5–22 min
late**. `concurrency` made it worse: GitHub keeps only one *pending* run per
group and silently cancels the queued one when a third arrives.

Current design: the job holds the runner and sleeps to each bar close itself.
The day is split into **two phases**, each started well ahead of the bars it
covers.

**Why two phases, not one job.** Starting 09:36 for a 09:45 first close left
only 9 minutes of slack. On 2026-08-26 GitHub started 43 min late and the 09:45
and 10:15 bars were lost. The cure is a long head start — but one job covering
07:30 → 15:16 IST is 7h46m and would breach GitHub's hard **6-hour job
ceiling**. Splitting the day buys each half ~2h of buffer *and* keeps it at
4h46m.

| | Phase A | Phase B |
|---|---|---|
| Closes | 09:45 → 12:15 IST | 12:45 → 15:15 IST |
| Cron (UTC) | `0,30 2-4 * * 1-5` | `0,30 5-7 * * 1-5` |
| Triggers (IST) | 07:30 … 10:00 | 10:30 … 13:00 |
| Concurrency | `session-morning` | `session-afternoon` |

- **Separate concurrency groups** so B is never queued behind a running A. The
  close ranges are disjoint, so concurrent phases cannot double-alert.
- `PHASE_FIRST_CLOSE` / `PHASE_LAST_CLOSE` bound `session_closes()`; unset means
  the full day, which is what `manual.yml` uses.
- `len(closes) < 2` on a *scheduled* run means a leftover queued job starting as
  the phase ends — it exits rather than duplicating the final bar. A *manual*
  run instead takes over the rest of the day, which is the recovery path.
- Losing a whole phase's triggers costs that half only, never the whole day.

**The repo must stay PUBLIC.** A daily 5h40m job needs ~7,300 min/month vs the
2,000 private allowance. Public repos get unlimited minutes.

Manual `workflow_dispatch` runs do a single immediate scan, keeping the bot
testable outside market hours.

Latency: alert lands ~30s after the candle closes.

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

## Modes

`BOT_MODE` selects the profile (default `nse`):

| | `nse` (production) | `crypto` (verification) |
|---|---|---|
| Market | NSE, 39 NIFTY50 symbols | BINANCE, `BTCUSDT` |
| Bars | 30-min, session-bound | 1-min, 24/7, time-boxed by `RUN_MINUTES` |
| Messages | every bar, incl. "no signals" | only on a real signal |

Crypto mode exists because a 24/7 market exercises the whole pipeline in
minutes instead of waiting for a session. At these parameters BTC 1-min
produces roughly **one signal every 10 minutes**. Trigger it from the Actions
tab: **Run workflow → mode: crypto**.

## Research findings — settled, do not re-litigate

Tested across futures, options and spot; 30-min, 15-min, 5-min, daily; six
months to sixteen years; thousands of parameter combinations with
out-of-sample splits throughout.

| Variant | Result |
|---|---|
| NIFTY futures, 30-min swing | +14% CAGR gross, but one lot forces 7.2% risk on ₹1L — untradeable at that size |
| Options (BS-modelled) | Swing: −97% drawdown. Intraday: looked good but rests on an unverifiable IV assumption |
| Spot delivery, daily, long-only | 4.2% CAGR / −33% DD vs **buy-and-hold 10.3% / −17%** — loses on both axes |
| Spot intraday MIS | Every parameter set net negative; costs (63 R) exceed gross edge (44 R) |

**The recurring arithmetic:** per-trade edge of +0.02 to +0.06 R against costs
of 0.04 to 0.12 R. Too many trades for the edge each carries. Not fixable by
tuning.

Two secondary findings worth keeping:
- **LinReg parameters do not affect signals.** In the Pine Script `buy`/`sell`
  derive solely from `src` and `xATRTrailingStop`. Only Key Value and ATR
  Period move signals; LinReg only recolours candles (the confidence label).
- **The colour/line filter subtracts value** at every timeframe tested — it
  enters later, not better.

## Reusable test harness

Built during the research and worth keeping for whatever strategy comes next.
Point them at new signal logic rather than rewriting:

| Script | Purpose |
|---|---|
| `backtest_index.py` | Single instrument, multi-timeframe |
| `optimise_index.py` | Grid search with in/out-of-sample split |
| `optimise_landscape.py` | Parameter heat-map — spots plateaus vs lucky spikes |
| `robustness_test.py` | Regime testing with realistic gap fills |
| `intraday_test.py` | Forced-flat-at-close variant |
| `rupee_simulation.py` | Itemised Indian charges on a real capital base |
| `spot_swing_backtest.py` | Portfolio sim with capital constraints |
| `options_backtest.py` | Black-Scholes option pricing |

**Always benchmark against buy-and-hold.** That comparison is what settled
this, and it was the last thing added rather than the first.

## Verification

```bash
python tv_signal_bot.py             # full run, sends to Telegram
BOT_MODE=crypto RUN_MINUTES=30 python tv_signal_bot.py   # fast live test
python verify_tvdatafeed_quality.py # print signals to compare against the chart
python measure_feed_lag.py          # re-measure feed lag
```

Data parity is checked by comparing `verify_tvdatafeed_quality.py` output
against the indicator on the user's TradingView chart — that is the ground truth.
