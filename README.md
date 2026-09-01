# NIFTY50 Signal Bot — TradingView → Telegram

BUY/SELL alerts from a **UT Bot + Linear Regression Candles** indicator, read
from **TradingView's own data feed** and delivered to Telegram.

Runs entirely on GitHub Actions. No PC left on, no TradingView Pro, no broker
subscription, no cost.

> ### ⚠️ The bot works. The strategy does not.
>
> The delivery pipeline is live and verified — alerts land ~25 seconds after
> each candle closes. But extensive backtesting (see **Research findings**)
> shows these signals carry **no tradeable edge after costs**. Buy-and-hold
> NIFTY beat the best variant on both return *and* drawdown.
>
> Treat the alerts as an indicator feed, not trade recommendations. The signal
> logic is meant to be replaced once a better strategy is found; everything
> else here is reusable as-is.

---

## Pipeline

```
GitHub Actions (cron) → tvDatafeed (TradingView websocket)
    → UT Bot + LinReg → Telegram Bot API → phone
```

---

## Why TradingView as the data source

The indicator was built and validated on a TradingView chart. Any other
provider introduces OHLC drift that shifts signal timing. `tvDatafeed` reads
TradingView's own websocket, so the bars are the ones that draw your chart —
parity by construction rather than approximation.

Verified against the live chart on NSE:RELIANCE 30-min: **8/8 signals matched**.

### Alternatives evaluated and rejected

| Source | Outcome |
|---|---|
| **NSEPython** | Dead twice over: NSE geo-blocks non-Indian IPs (HTTP 403), and its public API serves *daily* candles only — never 30-min. |
| **Zerodha enctoken** | Works, but expires every 24h with no refresh that survives a headless runner. |
| **Kite Connect** | ₹2,000/month for any historical access. |
| **yfinance** | Works, but OHLC drifts from TradingView. Kept dormant in `cloud_nifty_bot.py`. |

---

## How signals are evaluated

Signals come from the **last _closed_ 30-minute bar**, never the forming one.

The forming bar's close keeps moving until the bar completes, so evaluating it
causes **repainting** — an alert fires, then the bar closes the other way and
the signal vanishes. Only closed bars are used, so every alert is final.

The closed bar is chosen **by timestamp, not position**: a bar labelled `T`
closes at `T + 30min`, so the newest bar where `T + 30min <= now` is the one to
evaluate. Positional indexing (`-2`) silently picks the wrong bar whenever the
forming bar has not yet been emitted, which once made every alert a full bar
stale.

### Indicator parameters

These must mirror the Pine Script on the chart exactly.

| Parameter | Value |
|---|---|
| UT Bot — Key Value | `2` |
| UT Bot — ATR Period | `1` |
| LinReg — Signal Smoothing | `7` |
| LinReg — Simple MA (Signal Line) | `true` |
| LinReg — Linear Regression Length | `11` |

Confidence is `HIGH` when the LinReg candle colour agrees with the signal
direction (BUY+green / SELL+red), otherwise `MEDIUM`.

> **Only Key Value and ATR Period change which signals fire.** In the Pine
> Script, `buy`/`sell` derive solely from `src` and `xATRTrailingStop`. The
> LinReg values feed `plotcandle` and the signal line only — they recolour
> candles, which the bot reads as the confidence label, and nothing more.
> Verified: 6/8 and 7/11 both produced 49 BUY + 49 SELL over the same 1,000
> bars, differing only in the confidence split.

---

## Scheduling — the hard part

**The feed is not the constraint.** Measured lag from bar close to bar
availability is **~2 seconds** (median 2.4s, max 5.8s — see
`measure_feed_lag.py`).

**GitHub's scheduler is.** It is best-effort and, measured over 39 hours on a
24/7 BTC test, *sparse* rather than merely late: when it starts a run it is
4–30 minutes late (median 11), but it started only **10 runs against ~78
slots**, with gaps of 142–415 minutes.

Two designs were tried and abandoned in production:

| Design | Outcome |
|---|---|
| 12 short scheduled runs/day | ~half dropped; a full session alerted only on `:45` bars |
| One long job, 2 phases | 43-min delay lost the opening bars; 61%/phase, **15% silent days** |

### The governing fact

**GitHub fires one run per workflow per day, at or just after the LAST cron
slot** — not randomly from the window. Measured 31 Aug / 1 Sep 2026: +1, +5,
+30, +45, +54 minutes after the final slot.

**So where the window ends is what matters, not how wide it is.** An earlier
design widened windows by extending them *later*, putting phase 1's last
trigger at 10:23 — past the 09:45 and 10:15 closes. GitHub fired then, when
those bars were already unreachable: **0 of 12 bars on 31 Aug, 4 of 12 on
1 Sep.**

### Current design: four phases

One job per phase holds the runner and **sleeps to each bar close itself** — a
sleep is exact, a queue is not. Each phase's **last trigger sits 82–112 minutes
before its first close**, against a worst observed lateness of 54 minutes.

| Phase | Closes (IST) | Cron (UTC) | Triggers (IST) | Margin |
|---|---|---|---|---|
| 1 | 09:45, 10:15, 10:45 | `7,23,39,53 0-2` | 05:37 … 08:23 | 82 min |
| 2 | 11:15, 11:45, 12:15 | `7,23,39,53 1-3` | 06:37 … 09:23 | 112 min |
| 3 | 12:45, 13:15, 13:45 | `7,23,39,53 3-5` | 08:37 … 11:23 | 82 min |
| 4 | 14:15, 14:45, 15:15 | `7,23,39,53 4-6` | 09:37 … 12:23 | 112 min |

Replaying 31 Aug's real delays through these windows, all four phases start
before their first close and deliver 3/3 bars.

- **When changing a cron, check the last slot against the first close.** That
  one relationship decides whether a phase delivers anything.
- **Never schedule on `:00` or `:30`.** Those coincided with 8–10 hour delays
  on 27–28 Aug 2026; `:06/:36` only ever saw 1–22 minutes.
- Phases cover disjoint closes, so they cannot double-alert. Each has its own
  concurrency group so none queues behind another.
- **Every scheduled run sends a startup ping.** A phase GitHub never starts
  sends nothing — so a *missing* ping is how a skipped phase becomes visible.
- Job length is capped by the **6-hour ceiling**; current runs are 5.15–5.65h.

> **Requires a public repository.** Four daily multi-hour jobs far exceed the
> 2,000 minutes allowed on private repos. Public repos get unlimited Actions
> minutes, and nothing sensitive is in the code — Telegram credentials are
> repository Secrets and `.env` is gitignored.

### Getting to 100%

`repository_dispatch` is honoured **immediately** (measured 0s queue delay), so
an external scheduler posting `{"event_type":"scan"}` to
`/repos/OWNER/REPO/dispatches` removes GitHub's cron from the path entirely.
`manual.yml` already accepts it — it needs only a GitHub token with
**Contents: Read and write** stored in a free scheduler such as cron-job.org.

---

## Setup

### 1. Repository secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | From `@BotFather` |
| `TELEGRAM_CHAT_ID` | ✅ | From `@userinfobot` |
| `TV_USERNAME` / `TV_PASSWORD` | optional | Use your TradingView data entitlement instead of anonymous |

Anonymous TradingView access is **confirmed working from GitHub's US datacenter
IPs**. The credentials only matter if NSE data proves delayed.

### 2. Test it

**Actions → Manual / dispatch → Run workflow.** A Telegram message should
arrive within a minute — signals, or a "No signals" confirmation.

---

## Files

| File | Purpose |
|---|---|
| `tv_signal_bot.py` | The bot — fetch, evaluate, alert |
| `tradingview_indicators.py` | `ut_bot_alert()`, `linear_reg_candles()` |
| `.github/workflows/session-1..4.yml` | The four phase schedules |
| `.github/workflows/manual.yml` | `workflow_dispatch` + `repository_dispatch` |
| `verify_tvdatafeed_quality.py` | Re-check data parity against the chart |
| `measure_feed_lag.py` | Measure bar-close → availability lag |

That's the whole repo — five files and five workflows.

---

## Running it locally

```bash
pip install -r requirements.txt
# put TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env

python tv_signal_bot.py                    # scheduled-style session run
FORCE_SCAN=1 python tv_signal_bot.py       # one immediate scan
BOT_MODE=crypto RUN_MINUTES=30 python tv_signal_bot.py   # fast live test
```

### Modes

`BOT_MODE` selects the profile (default `nse`):

| | `nse` (production) | `crypto` (verification) |
|---|---|---|
| Market | NSE, 39 NIFTY50 symbols | BINANCE, `BTCUSDT` |
| Bars | 30-min, session-bound | 1/5/15-min (`TEST_INTERVAL`), 24/7 |
| Messages | every bar, incl. "no signals" | only on a real signal |

Crypto mode exists because a 24/7 market exercises the whole pipeline in
minutes rather than waiting for a session.

### Editing the watchlist

`SYMBOLS` in `tv_signal_bot.py` uses **TradingView** names, which occasionally
differ from other providers (`BAJAJ_AUTO`, not `BAJAJ-AUTO`). If a symbol
reports unavailable, check its spelling on TradingView.

---

## Research findings

Tested across futures, options and spot; 30-, 15-, 5-minute and daily bars; six
months to sixteen years; thousands of parameter combinations with
out-of-sample splits throughout.

| Variant | Result |
|---|---|
| NIFTY futures, 30-min swing | +14% CAGR gross, but one lot forces 7.2% risk on ₹1L — untradeable at that size |
| Options (Black-Scholes modelled) | Swing: −97% drawdown. Intraday: promising but rests on an unverifiable IV assumption |
| Spot delivery, daily, long-only | 4.2% CAGR / −33% DD vs **buy-and-hold 10.3% / −17%** — loses on both axes |
| Spot intraday MIS | Every parameter set net negative; costs (63 R) exceed gross edge (44 R) |

**The recurring arithmetic:** per-trade edge of +0.02 to +0.06 R against costs
of 0.04 to 0.12 R. Too many trades for the edge each carries — not fixable by
tuning.

The colour/line filter **subtracts** value at every timeframe tested: it enters
later, not better.

### The backtest code

Twelve scripts produced these results — grid search with out-of-sample splits,
parameter heat-maps, regime testing with realistic gap fills, portfolio
simulation under capital constraints, Black-Scholes option pricing, and an
itemised Indian-charges model.

They were **deleted once the research concluded**. Every one was wired to
`ut_bot_alert()`, so a different strategy would need them substantially
rewritten rather than reused. They remain in git history at commit `8ef3565`:

```bash
git show 8ef3565:optimise_index.py > optimise_index.py    # recover any of them
git show 8ef3565 --stat                                    # see the full list
```

**Always benchmark against buy-and-hold.** That comparison is what settled this,
and it was added last rather than first.

---

## Known limitations

- **~94% delivery, not 100%.** Roughly one bar in twenty may go missing when
  GitHub skips a phase's entire trigger window. The external trigger above is
  the only route to 100%.
- **NSE real-time vs delayed** depends on TradingView account entitlement, not
  on code. Every message prints `bar closed X min ago`: ~0.4 min means
  real-time, ~15 min means delayed.
- The `15:15` stub bar (15:15–15:30) is not consistently emitted as a 30-min bar.
- `tvdatafeed` installs from GitHub, not PyPI, and caps history at ~5,300 bars
  for 30-min data (~1.65 years).
