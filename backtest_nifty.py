"""
NIFTY50 backtest - UT Bot + LinReg on 5-minute bars
===================================================

Rules under test
----------------
* Indicator settings exactly as on the chart:
    Key Value 2 · ATR Period 1 · Signal Smoothing 7 · Simple MA on
    Linear Regression Length 11
* Signal-line filter: a BUY is taken only if the candle CLOSES ABOVE the
  white line, a SELL only if it CLOSES BELOW it. The white line is
  sma(linreg(close, 11), 7) - the same series the Pine Script plots.
* Fixed 1.5 : 1 reward-to-risk.

Modelling assumptions (each one materially affects the result, so they are
stated rather than buried):

1. ENTRY at the NEXT bar's open. The alert can only fire once the signal
   bar has closed, so its close is not a tradeable price.
2. STOP at the UT Bot trailing-stop level on the signal bar. That is the
   indicator's own risk level, so risk is defined by the strategy itself
   rather than by an arbitrary outside number.
3. TARGET at entry +/- 1.5 x risk.
4. EXIT on whichever comes first: stop, target, or an opposite signal
   (exited at that bar's close).
5. If a bar's range spans BOTH stop and target, the STOP is assumed to
   have been hit first. OHLC cannot resolve intrabar order, so this takes
   the pessimistic reading.
6. One position per symbol at a time. No costs or slippage modelled -
   results are in R multiples, not rupees.
"""

import logging
import sys
from datetime import datetime, timedelta

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

import numpy as np
import pandas as pd
import pytz

from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI

# ---------------------------------------------------------------- config

EXCHANGE = 'NSE'
INTERVAL = Interval.in_5_minute
N_BARS = 5000              # ~2 months of 5-min NSE bars (75/day)
LOOKBACK_DAYS = 60
RR = 1.5

KEY_VALUE = 2
ATR_PERIOD = 1
SIGNAL_LENGTH = 7
USE_SMA = True
LINREG_LENGTH = 11

IST = pytz.timezone('Asia/Kolkata')
LOCAL_TZ = datetime.now().astimezone().tzinfo

SYMBOLS = [
    'RELIANCE', 'TCS', 'INFY', 'WIPRO', 'SBIN', 'MARUTI', 'BAJAJ_AUTO',
    'LT', 'AXISBANK', 'BHARTIARTL', 'ITC', 'SUNPHARMA', 'ASIANPAINT',
    'HCLTECH', 'TECHM', 'ULTRACEMCO', 'JSWSTEEL', 'ICICIBANK', 'POWERGRID',
    'NESTLEIND', 'BAJAJFINSV', 'TITAN', 'HINDUNILVR', 'INDIGO',
    'ADANIPORTS', 'BRITANNIA', 'COALINDIA', 'ONGC', 'GAIL', 'NTPC', 'BPCL',
    'HEROMOTOCO', 'SIEMENS', 'SHRIRAMFIN', 'TATACONSUM', 'TATASTEEL',
    'EICHERMOT', 'HDFCBANK', 'KOTAKBANK',
]


def simulate(df, ut, lr, filter_mode):
    """
    Walk the bars and produce a list of closed trades.

    filter_mode:
      'none'   - take every UT Bot signal (baseline)
      'linreg' - require the LINREG candle to close beyond the white line
                 (what the chart actually draws)
      'price'  - require the REAL candle to close beyond the white line
    """
    o = df['open'].values
    h = df['high'].values
    lo = df['low'].values
    c = df['close'].values
    n = len(df)

    buy = ut['buy_signal']
    sell = ut['sell_signal']
    tstop = ut['trailing_stop']
    line = lr['signal']            # the white line
    lr_close = lr['close']

    trades = []
    i = 0
    while i < n - 1:
        is_buy, is_sell = bool(buy[i]), bool(sell[i])
        if not (is_buy or is_sell):
            i += 1
            continue

        # --- signal-line filter -------------------------------------
        if filter_mode != 'none':
            ref = lr_close[i] if filter_mode == 'linreg' else c[i]
            if np.isnan(line[i]) or np.isnan(ref):
                i += 1
                continue
            if is_buy and not (ref > line[i]):
                i += 1
                continue
            if is_sell and not (ref < line[i]):
                i += 1
                continue

        entry = o[i + 1]
        stop = tstop[i]
        if np.isnan(entry) or np.isnan(stop):
            i += 1
            continue

        direction = 1 if is_buy else -1
        risk = (entry - stop) if is_buy else (stop - entry)
        if risk <= 0:                      # stop on the wrong side
            i += 1
            continue
        target = entry + direction * RR * risk

        outcome = None
        exit_i = None
        for j in range(i + 1, n):
            if is_buy:
                hit_stop = lo[j] <= stop
                hit_target = h[j] >= target
            else:
                hit_stop = h[j] >= stop
                hit_target = lo[j] <= target

            if hit_stop:                   # pessimistic: stop wins ties
                outcome, exit_i = -1.0, j
                break
            if hit_target:
                outcome, exit_i = RR, j
                break
            if (is_buy and sell[j]) or (is_sell and buy[j]):
                outcome = direction * (c[j] - entry) / risk
                exit_i = j
                break

        if outcome is None:                # still open at the data edge
            break

        trades.append({
            'dir': 'BUY' if is_buy else 'SELL',
            'entry_i': i + 1, 'exit_i': exit_i,
            'entry': entry, 'exit_bars': exit_i - i,
            'R': outcome,
        })
        i = exit_i + 1                     # no overlapping positions

    return trades


def report(name, all_trades, symbols_used):
    tr = all_trades
    if not tr:
        print(f"\n  {name}: no trades")
        return None

    R = np.array([t['R'] for t in tr])
    wins = R[R > 0]
    losses = R[R <= 0]
    total = R.sum()
    win_rate = len(wins) / len(R) * 100
    expectancy = R.mean()

    # equity curve / drawdown in R
    eq = np.cumsum(R)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak).min()

    longs = [t for t in tr if t['dir'] == 'BUY']
    shorts = [t for t in tr if t['dir'] == 'SELL']

    print(f"\n{'=' * 74}")
    print(f"  {name}")
    print(f"{'=' * 74}")
    print(f"  Trades            : {len(R):>8}   across {symbols_used} symbols")
    print(f"  Win rate          : {win_rate:>7.1f}%   ({len(wins)}W / {len(losses)}L)")
    print(f"  Expectancy        : {expectancy:>+8.3f} R per trade")
    print(f"  Total             : {total:>+8.1f} R")
    print(f"  Max drawdown      : {dd:>+8.1f} R")
    print(f"  Avg win / loss    : {wins.mean() if len(wins) else 0:>+8.2f} R"
          f" / {losses.mean() if len(losses) else 0:>+.2f} R")
    print(f"  Avg bars held     : {np.mean([t['exit_bars'] for t in tr]):>8.1f}"
          f"   ({np.mean([t['exit_bars'] for t in tr]) * 5:.0f} min)")
    if longs:
        lr_ = np.array([t['R'] for t in longs])
        print(f"  Longs             : {len(longs):>8}   "
              f"{(lr_ > 0).mean() * 100:.1f}% win   {lr_.sum():+.1f} R")
    if shorts:
        sr_ = np.array([t['R'] for t in shorts])
        print(f"  Shorts            : {len(shorts):>8}   "
              f"{(sr_ > 0).mean() * 100:.1f}% win   {sr_.sum():+.1f} R")

    # break-even reference for this R:R
    be = 100 / (1 + RR)
    print(f"\n  Break-even win rate at {RR}:1 is {be:.1f}%  ->  "
          f"{'PROFITABLE' if win_rate > be else 'UNPROFITABLE'} "
          f"({win_rate - be:+.1f} pts)")
    return {'trades': len(R), 'win_rate': win_rate, 'total_R': total,
            'expectancy': expectancy, 'dd': dd}


def main():
    print("=" * 74)
    print("  NIFTY50 BACKTEST - UT Bot + LinReg, 5-minute bars")
    print("=" * 74)
    print(f"  Params : key={KEY_VALUE} atr={ATR_PERIOD} "
          f"signal={SIGNAL_LENGTH} sma={USE_SMA} linreg={LINREG_LENGTH}")
    print(f"  R:R    : {RR} : 1")
    print(f"  Window : last {LOOKBACK_DAYS} days\n")

    tv = TvDatafeed()
    cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)

    results = {'none': [], 'linreg': [], 'price': []}
    used = 0
    spans = []

    for k, sym in enumerate(SYMBOLS, 1):
        try:
            df = tv.get_hist(symbol=sym, exchange=EXCHANGE,
                             interval=INTERVAL, n_bars=N_BARS)
        except Exception as e:
            print(f"  [{k:2}/{len(SYMBOLS)}] {sym:<12} fetch failed "
                  f"({type(e).__name__})")
            continue
        if df is None or len(df) < 200:
            print(f"  [{k:2}/{len(SYMBOLS)}] {sym:<12} insufficient data")
            continue

        df = df[df.index >= cutoff]
        if len(df) < 200:
            print(f"  [{k:2}/{len(SYMBOLS)}] {sym:<12} too few bars in window")
            continue

        ut = TI.ut_bot_alert(df['high'].values, df['low'].values,
                             df['close'].values,
                             key_value=KEY_VALUE, atr_period=ATR_PERIOD)
        lr = TI.linear_reg_candles(df['open'].values, df['high'].values,
                                   df['low'].values, df['close'].values,
                                   signal_length=SIGNAL_LENGTH,
                                   use_sma=USE_SMA,
                                   linreg_length=LINREG_LENGTH)
        if ut is None or lr is None:
            continue

        for mode in results:
            results[mode].extend(simulate(df, ut, lr, mode))

        used += 1
        spans.append((df.index[0], df.index[-1]))
        print(f"  [{k:2}/{len(SYMBOLS)}] {sym:<12} {len(df):>5} bars  ok")

    if not used:
        print("\n  No usable data.")
        sys.exit(1)

    print(f"\n  Data window: {min(s[0] for s in spans)} -> "
          f"{max(s[1] for s in spans)}   ({used} symbols)")

    summary = {}
    summary['none'] = report(
        "BASELINE - every UT Bot signal (no filter)", results['none'], used)
    summary['linreg'] = report(
        "FILTERED - LinReg candle closes beyond the white line", results['linreg'], used)
    summary['price'] = report(
        "FILTERED - real price closes beyond the white line", results['price'], used)

    print(f"\n{'=' * 74}")
    print("  SIDE BY SIDE")
    print(f"{'=' * 74}")
    print(f"  {'variant':<34}{'trades':>8}{'win%':>8}{'total R':>10}{'exp R':>9}")
    for key, label in [('none', 'no filter (baseline)'),
                       ('linreg', 'filter: LinReg candle close'),
                       ('price', 'filter: real price close')]:
        s = summary.get(key)
        if s:
            print(f"  {label:<34}{s['trades']:>8}{s['win_rate']:>7.1f}%"
                  f"{s['total_R']:>+10.1f}{s['expectancy']:>+9.3f}")


if __name__ == "__main__":
    main()
