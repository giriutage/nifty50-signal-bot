# NIFTY50 Signal Bot

> **Status: the bot works; the strategy does not.**
>
> The pipeline is live and verified — alerts land ~25s after each candle
> closes. But extensive backtesting shows these signals carry **no tradeable
> edge after costs**; buy-and-hold NIFTY beat the best variant on both return
> and drawdown. Treat alerts as an indicator feed, not trade recommendations.
>
> The user paused in Aug 2026 to find a different strategy. **Do not resume
> parameter tuning on this one** — that ground is exhausted (see *Research*).

BUY/SELL alerts from a **UT Bot + Linear Regression Candles** indicator, read
from TradingView's own feed and delivered to Telegram via GitHub Actions.
No local machine, no TradingView Pro, no broker fees.

Repo: `giriutage/nifty50-signal-bot` — **must stay PUBLIC** (four daily
multi-hour jobs far exceed the 2,000-minute private allowance; public repos get
unlimited minutes). `README.md` carries the user-facing detail.

> An unrelated `CLAUDE.md` for a wealth-dashboard project lives at
> `e:\CLAUDECODE\.claude\CLAUDE.md` and also loads here. It does not apply.

## Pipeline

```
GitHub Actions (cron) → tvDatafeed (TradingView websocket)
    → TradingViewIndicators → Telegram Bot API → phone
```

| File | Role |
|---|---|
| `tv_signal_bot.py` | The bot: fetch → evaluate → alert |
| `tradingview_indicators.py` | `ut_bot_alert()`, `linear_reg_candles()` |
| `.github/workflows/session-1..4.yml` | The four phase schedules |
| `.github/workflows/manual.yml` | `workflow_dispatch` + `repository_dispatch` |
| `verify_tvdatafeed_quality.py` | Re-check data parity against the chart |
| `measure_feed_lag.py` | Measure bar-close → availability lag |

That is the whole repo. Twelve backtest scripts and a dormant yfinance
fallback were deleted once the research concluded — they were all wired to
`ut_bot_alert()`, so a different strategy would need them rewritten anyway.
**They live in git at `8ef3565`**; recover any with
`git show 8ef3565:<file> > <file>`.

## Invariants — do not break these

**1. Indicator parameters must mirror the Pine Script exactly.**
`key_value=2`, `atr_period=1`, `signal_length=7`, `use_sma=True`,
`linreg_length=11`. Changing one here without changing it on the chart
desynchronises every alert.

> Only **Key Value and ATR Period** move signals. In the Pine Script
> `buy`/`sell` derive solely from `src` and `xATRTrailingStop`; the LinReg
> values feed `plotcandle` and the signal line only, so they merely recolour
> candles — which the bot reads as the HIGH/MEDIUM confidence label. Verified:
> 6/8 and 7/11 both gave 49 BUY + 49 SELL over the same 1,000 bars.

**2. Only ever evaluate a CLOSED bar.** The forming bar's close keeps moving,
so evaluating it causes repainting — alerts that fire then vanish. The user
explicitly chose confirmed-only alerts over provisional ones.

**3. Select the closed bar by TIMESTAMP, never by position.** A bar labelled
`T` closes at `T + 30min`; take the newest where `T + 30min <= now`
(`last_closed_index()`). Positional `-2` was a real bug — it skipped a valid
closed bar whenever the forming bar had not yet been emitted, making every
alert one full 30-minute bar stale.

**4. Never commit `.env`.** It holds the Telegram token. Verify with
`git ls-tree -r --name-only origin/main` after any push.

## Data source: why tvDatafeed

The indicator was validated on a TradingView chart, so any other provider
introduces OHLC drift that shifts signal timing. tvDatafeed reads TradingView's
own websocket — parity by construction. Verified 8/8 signals against the live
chart on NSE:RELIANCE 30-min.

**Rejected — do not re-explore:**

| Source | Why |
|---|---|
| **NSEPython** | NSE geo-blocks non-Indian IPs (403 + HTML, surfacing as `KeyError: 'data'`), *and* its public API serves daily candles only. |
| **Zerodha enctoken** | Works, but expires every 24h with no refresh that survives a headless runner. |
| **Kite Connect** | ₹2,000/month for any historical access. |
| **yfinance** | Works, but OHLC drifts from TradingView. Kept dormant. |

## Scheduling — four phases, and why

**The feed is not the constraint** (~2s lag). **GitHub's scheduler is.**

**The governing fact — measured 31 Aug / 1 Sep 2026:** GitHub does *not* sample
randomly from a workflow's cron slots. It fires **one run per workflow per day,
at or just after the LAST slot** (+1, +5, +30, +45, +54 min observed).

So **where the window ENDS is what matters, not how wide it is.** A design that
widened windows by extending them later put phase 1's last trigger at 10:23 —
past the 09:45 and 10:15 closes — and GitHub duly fired then, when those bars
were already unreachable. It delivered **0/12 bars on 31 Aug, 4/12 on 1 Sep**.

Each phase's job holds the runner and **sleeps to each bar close itself** — a
sleep is exact, a queue is not. Each phase's LAST trigger sits **82–112 min
before its FIRST close**, against a worst observed lateness of 54 min.

| Phase | Closes (IST) | Cron (UTC) | Triggers (IST) | Margin |
|---|---|---|---|---|
| 1 | 09:45, 10:15, 10:45 | `7,23,39,53 0-2` | 05:37–08:23 | 82 min |
| 2 | 11:15, 11:45, 12:15 | `7,23,39,53 1-3` | 06:37–09:23 | 112 min |
| 3 | 12:45, 13:15, 13:45 | `7,23,39,53 3-5` | 08:37–11:23 | 82 min |
| 4 | 14:15, 14:45, 15:15 | `7,23,39,53 4-6` | 09:37–12:23 | 112 min |

Replaying 31 Aug's actual delays through these windows: all four phases start
before their first close and deliver 3/3 bars.

**When changing a cron, check the LAST slot against the FIRST close** — that
single relationship decides whether a phase delivers anything. Widening a
window later is actively harmful. Job length is capped by the **6-hour
ceiling** (current runs 5.15–5.65h).

Earlier failures: 12 short runs/day (half dropped); one long job in 2 phases
(43-min delay lost the opening bars); 4 phases with late-extending windows
(0–33% delivery).

- **Never schedule on `:00` or `:30`** — those coincided with 8–10 hour delays
  on 27–28 Aug 2026; `:06/:36` only ever saw 1–22 min.
- Closes are disjoint, so overlapping phases cannot double-alert. Per-phase
  concurrency groups stop any phase queueing behind another.
- No "leftover run" guard is needed: `cancel-in-progress: false` means a second
  run of a phase starts only after the first ends, by which time its closes
  have passed and `not closes` ends it.
- **Every scheduled run sends a startup ping**, so a *missing* ping is how a
  skipped phase becomes visible.
- `PHASE_FIRST_CLOSE` / `PHASE_LAST_CLOSE` bound `session_closes()`; unset =
  full day, which `manual.yml` uses so a manual run recovers a whole session.

**To reach 100%:** `repository_dispatch` is honoured immediately (0s queue), so
an external scheduler removes GitHub cron from the path. `manual.yml` already
accepts it; needs a user-created token (Contents: read/write) in e.g.
cron-job.org. Blocked on the user, not on code.

## Gotchas

- **`git` is not on PATH.** Use `& "C:\Program Files\Git\bin\git.exe"`.
- **The repo is public — the Actions API needs no auth.** Query
  `/repos/giriutage/nifty50-signal-bot/actions/runs` to diagnose scheduling
  directly rather than asking the user to read the Actions tab.
- **`tvdatafeed` installs from GitHub, not PyPI**:
  `git+https://github.com/rongardF/tvdatafeed.git`. Caps 30-min history at
  ~5,300 bars (~1.65 years).
- **tvDatafeed returns naive timestamps in the machine's local timezone** — UTC
  on Actions, CEST locally. Localise with `LOCAL_TZ`, convert to IST. Affects
  display only; indicator math depends on bar order alone.
- **TradingView symbol names differ** — `BAJAJ_AUTO`, not `BAJAJ-AUTO`.
  `fetch()` retries transient empty responses.
- **The `15:15` stub bar** is not consistently emitted as a 30-min bar.
- **Telegram sends retry with backoff** — a transient timeout must never drop a
  signal.
- Python here is `pythoncore-3.14` (system), not a venv.
- **Deploy schedule changes outside market hours.** Pushing the two-phase split
  mid-session on 26 Aug left the old job running alongside a new phase and
  duplicated six alerts.

## Modes

`BOT_MODE` (default `nse`): `crypto` switches to BINANCE `BTCUSDT`,
`TEST_INTERVAL` (1/5/15) minute bars, 24/7, time-boxed by `RUN_MINUTES`, and
stays quiet unless a real signal fires. It exists because a 24/7 market
exercises the whole pipeline in minutes rather than waiting for a session.

## Research — settled, do not re-litigate

Futures, options and spot; 30/15/5-min and daily; six months to sixteen years;
thousands of parameter combinations with out-of-sample splits throughout. Best
spot variant: 4.2% CAGR / −33% DD against **buy-and-hold 10.3% / −17%**.

**The recurring arithmetic:** per-trade edge +0.02 to +0.06 R against costs of
0.04 to 0.12 R. Too many trades for the edge each carries — not fixable by
tuning. The colour/line filter *subtracts* value at every timeframe.

Full result tables are in `README.md`. **Always benchmark against
buy-and-hold** — that comparison settled this, and it was added last rather
than first.

## Verification

```bash
python tv_signal_bot.py                    # session-style run
FORCE_SCAN=1 python tv_signal_bot.py       # one immediate scan
BOT_MODE=crypto RUN_MINUTES=30 python tv_signal_bot.py
python verify_tvdatafeed_quality.py        # compare signals against the chart
python measure_feed_lag.py                 # re-measure feed lag
```

Data parity is checked by comparing `verify_tvdatafeed_quality.py` output
against the indicator on the user's TradingView chart — that is ground truth.
