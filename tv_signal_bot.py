"""
NIFTY50 Signal Bot - TradingView data source (tvDatafeed)
=========================================================
Pulls 30-min OHLC straight from TradingView's own feed, runs the
UT Bot + LinReg Candles logic, and pushes BUY/SELL alerts to Telegram.

Timing design
-------------
Measured feed lag from bar close to bar availability is ~2 seconds. The
real latency risk is GitHub Actions' scheduler, which queues jobs on a
best-effort basis and can start them many minutes late.

So the workflow fires a few minutes BEFORE each bar closes, and this
script waits until just past the boundary before scanning. That turns
unpredictable scheduler jitter into a controlled wait. If the job is
started late anyway, the wait is skipped and the scan runs immediately.

Correctness
-----------
Signals are read from the last CLOSED bar, selected by timestamp rather
than position: a bar labelled T closes at T + 30min, so the newest bar
with (T + 30min) <= now is the one to evaluate. Positional indexing
(-2) silently picks the wrong bar whenever the forming bar has not yet
been emitted. The forming bar is never evaluated, which is what prevents
repainting - alerts that appear and then vanish as the candle finishes.
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytz
import requests

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators

# ---------------------------------------------------------------- config

# Local convenience only; on GitHub Actions these arrive as repository Secrets.
try:
    from dotenv import load_dotenv
    load_dotenv('.env')
except Exception:
    pass

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TV_USERNAME = os.getenv('TV_USERNAME')
TV_PASSWORD = os.getenv('TV_PASSWORD')

N_BARS = 300
RETRIES = 2

IST = pytz.timezone('Asia/Kolkata')
LOCAL_TZ = datetime.now().astimezone().tzinfo   # tvDatafeed stamps bars in local tz

# --- Indicator parameters -------------------------------------------------
# These MUST mirror the Pine Script on the chart exactly. Changing one here
# without changing it there desynchronises every alert.
#   Signal Smoothing 7 · Simple MA on · Lin Reg on
#   Linear Regression Length 11 · Key Value 2 · ATR Period 1
UT_KEY_VALUE = 2
UT_ATR_PERIOD = 1
LR_SIGNAL_LENGTH = 7
LR_USE_SMA = True
LR_LINREG_LENGTH = 11

NIFTY50 = [
    'RELIANCE', 'TCS', 'INFY', 'WIPRO', 'SBIN', 'MARUTI', 'BAJAJ_AUTO',
    'LT', 'AXISBANK', 'BHARTIARTL', 'ITC', 'SUNPHARMA', 'ASIANPAINT',
    'HCLTECH', 'TECHM', 'ULTRACEMCO', 'JSWSTEEL', 'ICICIBANK', 'POWERGRID',
    'NESTLEIND', 'BAJAJFINSV', 'TITAN', 'HINDUNILVR', 'INDIGO',
    'ADANIPORTS', 'BRITANNIA', 'COALINDIA', 'ONGC', 'GAIL', 'NTPC', 'BPCL',
    'HEROMOTOCO', 'SIEMENS', 'SHRIRAMFIN', 'TATACONSUM', 'TATASTEEL',
    'EICHERMOT', 'HDFCBANK', 'KOTAKBANK',
]

# --- Mode -----------------------------------------------------------------
# 'nse'    production: NIFTY50, 30-min bars, bound to the trading session.
# 'crypto' verification: BTCUSDT 1-min on a 24/7 market, so the whole
#          pipeline can be exercised in minutes rather than waiting for a
#          session. Stays quiet unless there is a real signal - at one bar a
#          minute, "no signals" messages would be pure spam.
MODE = os.getenv('BOT_MODE', 'nse').strip().lower()

if MODE == 'crypto':
    EXCHANGE = 'BINANCE'
    SYMBOLS = [s.strip() for s in
               os.getenv('TEST_SYMBOLS', 'BTCUSDT').split(',') if s.strip()]
    INTERVAL = Interval.in_1_minute
    INTERVAL_MINUTES = 1
    SETTLE_SECONDS = 8
    SESSION_BOUND = False       # 24/7 market: no session window, no date guard
    QUIET = True                # message only when there is an actual signal
    CURRENCY = '$'
    RUN_MINUTES = int(os.getenv('RUN_MINUTES', '45'))
else:
    EXCHANGE = 'NSE'
    SYMBOLS = NIFTY50
    INTERVAL = Interval.in_30_minute
    INTERVAL_MINUTES = 30
    SETTLE_SECONDS = 25         # measured feed lag is ~2s; 25s is generous
    SESSION_BOUND = True
    QUIET = False
    CURRENCY = '₹'
    RUN_MINUTES = None

# NSE 30-min bars close at :15 and :45 IST.
BOUNDARY_MINUTES = (15, 45)


def log(msg):
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')} IST] {msg}", flush=True)


# ------------------------------------------------------------ bar timing

def session_closes(now=None):
    """
    Bar-close times still ahead of us, as IST datetimes.

    NSE: the 12 closes 09:45, 10:15 ... 15:15 today. A close that has just
    passed (within GRACE) is still included so a slightly late start does
    not skip the bar it was meant to catch.

    Crypto: the next RUN_MINUTES one-minute boundaries - a 24/7 market has
    no session, so the run is simply time-boxed.
    """
    now = now or datetime.now(IST)

    if not SESSION_BOUND:
        base = now.replace(second=0, microsecond=0)
        return [base + timedelta(minutes=i) for i in range(1, RUN_MINUTES + 1)]

    GRACE = timedelta(minutes=2)
    if now.weekday() > 4:
        return []

    out = []
    for hour in range(9, 16):
        for minute in BOUNDARY_MINUTES:
            t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if t < now.replace(hour=9, minute=45, second=0, microsecond=0):
                continue                      # before the first close
            if t > now.replace(hour=15, minute=15, second=0, microsecond=0):
                continue                      # after the last close
            if t + GRACE >= now:
                out.append(t)
    return sorted(out)


def sleep_until(target):
    """Sleep until `target`, reporting the wait. Returns False if already past."""
    wait = (target - datetime.now(IST)).total_seconds()
    if wait <= 0:
        return False
    log(f"Sleeping {wait/60:.1f} min until the "
        f"{(target - timedelta(seconds=SETTLE_SECONDS)).strftime('%H:%M')} "
        f"bar close...")
    time.sleep(wait)
    return True


def last_closed_index(df):
    """
    Index of the newest bar that has actually closed.

    A bar labelled T spans T -> T + INTERVAL_MINUTES, so it is closed once
    now >= T + INTERVAL_MINUTES. Returns (index, bar_time_ist, staleness_s)
    or (None, None, None).
    """
    idx_ist = df.index.tz_localize(LOCAL_TZ).tz_convert(IST)
    close_times = idx_ist + pd.Timedelta(minutes=INTERVAL_MINUTES)
    now = datetime.now(IST)

    closed = np.where(close_times <= now)[0]
    if len(closed) == 0:
        return None, None, None

    i = int(closed[-1])
    staleness = (now - close_times[i]).total_seconds()
    return i, idx_ist[i], staleness


# --------------------------------------------------------------- feed

def connect():
    """Open a TradingView session, logging in when credentials are supplied."""
    if TV_USERNAME and TV_PASSWORD:
        log("Connecting to TradingView (authenticated)...")
        try:
            return TvDatafeed(username=TV_USERNAME, password=TV_PASSWORD)
        except Exception as e:
            log(f"  Login failed ({e}); falling back to anonymous.")
    log("Connecting to TradingView (anonymous)...")
    return TvDatafeed()


def fetch(tv, symbol):
    """Fetch bars for one symbol, retrying transient empty responses."""
    for attempt in range(RETRIES + 1):
        try:
            df = tv.get_hist(symbol=symbol, exchange=EXCHANGE,
                             interval=INTERVAL, n_bars=N_BARS)
            if df is not None and len(df) >= 50:
                return df
        except Exception as e:
            if attempt == RETRIES:
                log(f"  {symbol}: {type(e).__name__}: {str(e)[:60]}")
        time.sleep(0.4)
    return None


def evaluate(df):
    """Run the indicators against the last closed bar."""
    i, bar_time, staleness = last_closed_index(df)
    if i is None or i < 1:
        return None, None, None

    ut = TradingViewIndicators.ut_bot_alert(
        df['high'].values, df['low'].values, df['close'].values,
        key_value=UT_KEY_VALUE, atr_period=UT_ATR_PERIOD)

    lr = TradingViewIndicators.linear_reg_candles(
        df['open'].values, df['high'].values, df['low'].values, df['close'].values,
        signal_length=LR_SIGNAL_LENGTH, use_sma=LR_USE_SMA,
        linreg_length=LR_LINREG_LENGTH)

    if ut is None or lr is None:
        return None, bar_time, staleness

    is_buy = bool(ut['buy_signal'][i])
    is_sell = bool(ut['sell_signal'][i])
    if not (is_buy or is_sell):
        return None, bar_time, staleness

    green = bool(lr['green_candles'][i])
    red = bool(lr['red_candles'][i])
    price = float(df['close'].values[i])

    if is_buy:
        return ({'type': 'BUY', 'price': price,
                 'confidence': 'HIGH' if green else 'MEDIUM',
                 'bar_time': bar_time}, bar_time, staleness)
    return ({'type': 'SELL', 'price': price,
             'confidence': 'HIGH' if red else 'MEDIUM',
             'bar_time': bar_time}, bar_time, staleness)


# ------------------------------------------------------------- delivery

def send_telegram(text, attempts=4):
    """
    Deliver to Telegram, retrying transient failures with backoff.

    A network blip must not lose a signal, so this is deliberately
    persistent rather than failing on the first timeout.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'HTML'}

    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.json().get('ok'):
                return True
            log(f"Telegram rejected the message: {str(r.text)[:120]}")
        except Exception as e:
            log(f"Telegram attempt {attempt}/{attempts} failed: "
                f"{type(e).__name__}")
        if attempt < attempts:
            time.sleep(2 ** attempt)   # 2s, 4s, 8s
    return False


def build_message(signals, scanned, failed, bar_time, staleness):
    now_ist = datetime.now(IST)
    bar = bar_time.strftime('%H:%M') if bar_time is not None else '--:--'
    age = f"{staleness:.0f}s" if staleness is not None and staleness < 120 \
        else (f"{staleness/60:.1f} min" if staleness is not None else "?")
    title = "BTC TEST" if MODE == 'crypto' else "NIFTY50"

    footer = (f"<i>{now_ist.strftime('%d %b %H:%M:%S')} IST · "
              f"{scanned} scanned · bar closed {age} ago"
              + (f" · {failed} unavailable" if failed else "") + "</i>")

    if not signals:
        return f"\U0001F4A4 <b>No signals</b> · {bar} bar\n{footer}"

    lines = [f"<b>\U0001F4CA {title} · {bar} bar</b>", ""]
    for s in signals:
        icon = "\U0001F7E2" if s['type'] == 'BUY' else "\U0001F534"
        lines.append(f"{icon} <b>{s['symbol']}</b> — {s['type']}")
        lines.append(f"    {CURRENCY}{s['price']:,.2f} · {s['confidence']} confidence")
        lines.append("")
    lines.append(footer)
    return "\n".join(lines)


def run_one_scan():
    """
    Scan every symbol against its last closed bar and alert once.

    Opens a fresh TradingView session each time: a websocket held open
    across a whole trading day is not reliable, and reconnecting costs
    under a second.
    """
    tv = connect()

    signals, scanned, failed = [], 0, 0
    bar_time = staleness = None
    log(f"Scanning {len(SYMBOLS)} symbols...")

    for sym in SYMBOLS:
        df = fetch(tv, sym)
        if df is None:
            failed += 1
            continue
        scanned += 1
        try:
            sig, bt, st = evaluate(df)
        except Exception as e:
            log(f"  {sym}: evaluate failed - {type(e).__name__}: {str(e)[:50]}")
            continue
        if bt is not None and bar_time is None:
            bar_time, staleness = bt, st
        if sig:
            sig['symbol'] = sym
            signals.append(sig)
            log(f"  >> {sig['type']:<4} {sym} @ {CURRENCY}{sig['price']:,.2f} "
                f"({sig['confidence']})")

    # A bar from a previous session means we are not in a live market -
    # alerting on it would replay yesterday's signals as though new. Only
    # meaningful for a session-bound market; a 24/7 one legitimately crosses
    # midnight.
    if (SESSION_BOUND and bar_time is not None
            and bar_time.date() != datetime.now(IST).date()):
        log(f"Newest closed bar is {bar_time.strftime('%Y-%m-%d %H:%M')} IST, "
            f"not today. Staying silent.")
        return

    # In quiet mode an uneventful bar produces no message at all.
    if QUIET and not signals:
        log(f"  no signal on the {bar_time.strftime('%H:%M') if bar_time else '--:--'} bar")
        return

    if bar_time is not None:
        log(f"Evaluated bar {bar_time.strftime('%H:%M')} IST "
            f"(closed {staleness/60:.1f} min ago)")
    log(f"Scanned {scanned}/{len(SYMBOLS)} · {len(signals)} signal(s)"
        + (f" · {failed} unavailable" if failed else ""))

    if send_telegram(build_message(signals, scanned, failed, bar_time, staleness)):
        log("Telegram sent.")
    else:
        log("Telegram FAILED - giving up on this bar.")


def main():
    log("=" * 60)
    log("NIFTY50 Signal Bot · TradingView feed")
    log("=" * 60)

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("FATAL: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set.")
        sys.exit(1)

    log(f"Mode: {MODE} · {EXCHANGE} · {INTERVAL_MINUTES}-min bars · "
        f"{len(SYMBOLS)} symbol(s)")
    log(f"Params: key={UT_KEY_VALUE} atr={UT_ATR_PERIOD} "
        f"signal={LR_SIGNAL_LENGTH} sma={LR_USE_SMA} linreg={LR_LINREG_LENGTH}")

    # A manual NSE run scans once, immediately, so the bot stays testable
    # outside market hours without occupying a runner all day. Crypto mode is
    # itself a test, so it always runs its loop.
    if SESSION_BOUND and (os.getenv('GITHUB_EVENT_NAME') == 'workflow_dispatch'
                          or os.getenv('FORCE_SCAN') == '1'):
        log("Manual run - single scan.")
        run_one_scan()
        log("Done.")
        return

    # Scheduled run: hold the runner for the session and wake at each bar
    # close. Sleeping here rather than relying on GitHub's scheduler is the
    # whole point - cron was observed dropping runs and starting 5-22 min
    # late, while a sleep is exact.
    closes = session_closes()
    if not closes:
        log("No bar closes left today (weekend, holiday, or after 15:15 IST).")
        return

    # Redundant start triggers mean one job can sit queued behind the live
    # one and only begin as the session ends. Such a leftover would re-alert
    # the final bar. A genuine start always has most of the day ahead of it,
    # so a near-empty schedule identifies the leftover.
    if SESSION_BOUND and len(closes) < 2:
        log(f"Only {len(closes)} close left - this is a leftover queued run. "
            f"Exiting rather than duplicating the final bar.")
        return

    log(f"Session mode: {len(closes)} bar closes ahead "
        f"({closes[0].strftime('%H:%M')} -> {closes[-1].strftime('%H:%M')} IST)")

    for n, close_t in enumerate(closes, 1):
        target = close_t + timedelta(seconds=SETTLE_SECONDS)
        sleep_until(target)
        log("-" * 60)
        log(f"[{n}/{len(closes)}] {close_t.strftime('%H:%M')} bar close")
        try:
            run_one_scan()
        except Exception as e:
            # One bad bar must never end the session.
            log(f"Scan failed: {type(e).__name__}: {str(e)[:80]}")

    log("=" * 60)
    log("Session complete.")


if __name__ == "__main__":
    main()
