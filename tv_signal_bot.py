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

EXCHANGE = 'NSE'
INTERVAL = Interval.in_30_minute
INTERVAL_MINUTES = 30
N_BARS = 300
RETRIES = 2

# Bar boundaries for a 09:15-open exchange fall on :15 and :45.
BOUNDARY_MINUTES = (15, 45)
SETTLE_SECONDS = 25      # measured feed lag is ~2s; 25s is a generous margin
MAX_WAIT_SECONDS = 360   # beyond this we assume a late start and scan at once

IST = pytz.timezone('Asia/Kolkata')
LOCAL_TZ = datetime.now().astimezone().tzinfo   # tvDatafeed stamps bars in local tz

# Indicator parameters - must mirror the Pine Script exactly
UT_KEY_VALUE = 2
UT_ATR_PERIOD = 1
LR_SIGNAL_LENGTH = 6
LR_USE_SMA = True
LR_LINREG_LENGTH = 8

SYMBOLS = [
    'RELIANCE', 'TCS', 'INFY', 'WIPRO', 'SBIN', 'MARUTI', 'BAJAJ_AUTO',
    'LT', 'AXISBANK', 'BHARTIARTL', 'ITC', 'SUNPHARMA', 'ASIANPAINT',
    'HCLTECH', 'TECHM', 'ULTRACEMCO', 'JSWSTEEL', 'ICICIBANK', 'POWERGRID',
    'NESTLEIND', 'BAJAJFINSV', 'TITAN', 'HINDUNILVR', 'INDIGO',
    'ADANIPORTS', 'BRITANNIA', 'COALINDIA', 'ONGC', 'GAIL', 'NTPC', 'BPCL',
    'HEROMOTOCO', 'SIEMENS', 'SHRIRAMFIN', 'TATACONSUM', 'TATASTEEL',
    'EICHERMOT', 'HDFCBANK', 'KOTAKBANK',
]


def log(msg):
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')} IST] {msg}", flush=True)


# ------------------------------------------------------------ bar timing

def next_boundary(now):
    """The next 30-minute bar boundary at or after `now`."""
    for m in BOUNDARY_MINUTES:
        if now.minute < m:
            return now.replace(minute=m, second=0, microsecond=0)
    return (now + timedelta(hours=1)).replace(
        minute=BOUNDARY_MINUTES[0], second=0, microsecond=0)


def wait_for_bar_close():
    """
    Sleep until just past the next bar boundary so the closing bar is
    settled. Skipped entirely when the boundary is too far away, which
    means the scheduler started us late and we should scan immediately.
    """
    now = datetime.now(IST)
    target = next_boundary(now) + timedelta(seconds=SETTLE_SECONDS)
    wait = (target - now).total_seconds()

    if wait <= 0:
        return
    if wait > MAX_WAIT_SECONDS:
        log(f"Started late (next close is {wait/60:.1f} min away) - "
            f"scanning the most recent closed bar now.")
        return

    log(f"Waiting {wait:.0f}s for the {target.strftime('%H:%M')} bar close...")
    time.sleep(wait)


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
    age = f"{staleness/60:.1f} min" if staleness is not None else "?"

    footer = (f"<i>{now_ist.strftime('%d %b %H:%M')} IST · "
              f"{scanned} scanned · bar closed {age} ago"
              + (f" · {failed} unavailable" if failed else "") + "</i>")

    if not signals:
        return f"\U0001F4A4 <b>No signals</b> · {bar} bar\n{footer}"

    lines = [f"<b>\U0001F4CA NIFTY50 · {bar} bar</b>", ""]
    for s in signals:
        icon = "\U0001F7E2" if s['type'] == 'BUY' else "\U0001F534"
        lines.append(f"{icon} <b>{s['symbol']}</b> — {s['type']}")
        lines.append(f"    ₹{s['price']:,.2f} · {s['confidence']} confidence")
        lines.append("")
    lines.append(footer)
    return "\n".join(lines)


def main():
    log("=" * 60)
    log("NIFTY50 Signal Bot · TradingView feed")
    log("=" * 60)

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("FATAL: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set.")
        sys.exit(1)

    wait_for_bar_close()

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
            log(f"  >> {sig['type']:<4} {sym} @ Rs.{sig['price']:,.2f} "
                f"({sig['confidence']})")

    if bar_time is not None:
        log(f"Evaluated bar {bar_time.strftime('%H:%M')} IST "
            f"(closed {staleness/60:.1f} min ago)")
    log(f"Scanned {scanned}/{len(SYMBOLS)} · {len(signals)} signal(s)"
        + (f" · {failed} unavailable" if failed else ""))

    if send_telegram(build_message(signals, scanned, failed, bar_time, staleness)):
        log("Telegram sent.")
    else:
        log("Telegram FAILED.")
        sys.exit(1)

    log("Done.")


if __name__ == "__main__":
    main()
