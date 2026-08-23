"""
NIFTY50 Signal Bot - TradingView data source (tvDatafeed)
=========================================================
Pulls 30-min OHLC straight from TradingView's own feed, runs the
UT Bot + LinReg Candles logic, and pushes BUY/SELL alerts to Telegram.

Design notes
------------
* Signals are evaluated on the last CLOSED bar (index -2), never the
  forming bar (-1). This eliminates repainting: every alert is final.
* Runs stateless on GitHub Actions. Because each scheduled run maps to
  exactly one freshly-closed bar, no cross-run state is needed.
* TradingView login is optional. Anonymous access works, but supplying
  TV_USERNAME / TV_PASSWORD makes the feed match your own chart's
  data entitlement exactly.
"""

import os
import sys
import time
import logging
from datetime import datetime

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
N_BARS = 300
RETRIES = 2

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
    """
    Run the indicators and inspect the last CLOSED bar.

    Returns a signal dict, or None when that bar produced nothing.
    """
    ut = TradingViewIndicators.ut_bot_alert(
        df['high'].values, df['low'].values, df['close'].values,
        key_value=UT_KEY_VALUE, atr_period=UT_ATR_PERIOD)

    lr = TradingViewIndicators.linear_reg_candles(
        df['open'].values, df['high'].values, df['low'].values, df['close'].values,
        signal_length=LR_SIGNAL_LENGTH, use_sma=LR_USE_SMA,
        linreg_length=LR_LINREG_LENGTH)

    if ut is None or lr is None:
        return None

    i = len(df) - 2          # last CLOSED bar; -1 is still forming
    if i < 1:
        return None

    is_buy = bool(ut['buy_signal'][i])
    is_sell = bool(ut['sell_signal'][i])
    if not (is_buy or is_sell):
        return None

    green = bool(lr['green_candles'][i])
    red = bool(lr['red_candles'][i])

    bar_ist = df.index[i].tz_localize(LOCAL_TZ).astimezone(IST)

    if is_buy:
        return {'type': 'BUY', 'price': float(df['close'].values[i]),
                'confidence': 'HIGH' if green else 'MEDIUM', 'bar_time': bar_ist}
    return {'type': 'SELL', 'price': float(df['close'].values[i]),
            'confidence': 'HIGH' if red else 'MEDIUM', 'bar_time': bar_ist}


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={'chat_id': TELEGRAM_CHAT_ID, 'text': text,
                                     'parse_mode': 'HTML'}, timeout=15)
        return r.json().get('ok', False)
    except Exception as e:
        log(f"Telegram error: {e}")
        return False


def build_message(signals, scanned, failed):
    now_ist = datetime.now(IST)
    if not signals:
        return (f"\U0001F4A4 <b>No signals</b>\n"
                f"<i>{now_ist.strftime('%d %b %H:%M')} IST · "
                f"{scanned} scanned</i>")

    bar = signals[0]['bar_time'].strftime('%H:%M')
    lines = [f"<b>\U0001F4CA NIFTY50 · {bar} IST bar</b>", ""]
    for s in signals:
        icon = "\U0001F7E2" if s['type'] == 'BUY' else "\U0001F534"
        lines.append(f"{icon} <b>{s['symbol']}</b> — {s['type']}")
        lines.append(f"    ₹{s['price']:,.2f} · {s['confidence']} confidence")
        lines.append("")
    lines.append(f"<i>{now_ist.strftime('%d %b %H:%M')} IST · "
                 f"{scanned} scanned"
                 + (f" · {failed} unavailable" if failed else "") + "</i>")
    return "\n".join(lines)


def main():
    log("=" * 60)
    log("NIFTY50 Signal Bot · TradingView feed")
    log("=" * 60)

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("FATAL: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set.")
        sys.exit(1)

    tv = connect()

    signals, scanned, failed = [], 0, 0
    log(f"Scanning {len(SYMBOLS)} symbols on the last closed 30-min bar...")

    for sym in SYMBOLS:
        df = fetch(tv, sym)
        if df is None:
            failed += 1
            continue
        scanned += 1
        try:
            sig = evaluate(df)
        except Exception as e:
            log(f"  {sym}: evaluate failed - {type(e).__name__}: {str(e)[:50]}")
            continue
        if sig:
            sig['symbol'] = sym
            signals.append(sig)
            icon = "BUY " if sig['type'] == 'BUY' else "SELL"
            log(f"  >> {icon} {sym} @ Rs.{sig['price']:,.2f} "
                f"({sig['confidence']}) bar={sig['bar_time'].strftime('%H:%M')}")

    log(f"Scanned {scanned}/{len(SYMBOLS)} · {len(signals)} signal(s)"
        + (f" · {failed} unavailable" if failed else ""))

    if send_telegram(build_message(signals, scanned, failed)):
        log("Telegram sent.")
    else:
        log("Telegram FAILED.")
        sys.exit(1)

    log("Done.")


if __name__ == "__main__":
    main()
