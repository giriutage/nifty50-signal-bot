"""
NIFTY 50 INDEX backtest - 5 / 15 / 30-minute, last 6 months
===========================================================

Entry rules (symmetric, as specified)
-------------------------------------
  BUY  : UT Bot buy signal  AND  LinReg candle GREEN  AND  close ABOVE the white line
  SELL : UT Bot sell signal AND  LinReg candle RED    AND  close BELOW the white line

The white line is sma(linreg(close, 11), 7) - the series the Pine Script
plots. "Candle" means the LinReg candle the chart draws: green when
bclose > bopen, red when bclose < bopen.

Settings unchanged: Key Value 2 · ATR Period 1 · Signal Smoothing 7 ·
Simple MA on · Linear Regression Length 11 · R:R 1.5 : 1

Modelling assumptions (each affects the result, so stated openly)
----------------------------------------------------------------
1. ENTRY at the next bar's open - the signal bar's close is not tradeable.
2. STOP at the UT Bot trailing-stop level on the signal bar.
3. TARGET at entry +/- 1.5 x risk.
4. Both exit rules are reported: with and without an opposite-signal exit,
   because that rule dominated the previous test.
5. A bar spanning both stop and target counts as a STOP. OHLC cannot
   resolve intrabar order, so this reads pessimistically.
6. One position at a time. No costs or slippage - results are in R.
"""

import logging
from datetime import datetime, timedelta

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

import numpy as np
import pandas as pd
from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI

# ---------------------------------------------------------------- config

INDEX_CANDIDATES = ['NIFTY', 'NIFTY50', 'CNXNIFTY']
EXCHANGE = 'NSE'
LOOKBACK_DAYS = 183          # ~6 months
RR = 1.5

KEY_VALUE, ATR_PERIOD = 2, 1
SIGNAL_LENGTH, USE_SMA, LINREG_LENGTH = 7, True, 11

TIMEFRAMES = [
    ('5-min',  Interval.in_5_minute,  5,  12000),
    ('15-min', Interval.in_15_minute, 15, 6000),
    ('30-min', Interval.in_30_minute, 30, 4000),
]


def fetch(tv, interval, n_bars):
    """Resolve the index symbol and pull history."""
    for sym in INDEX_CANDIDATES:
        try:
            df = tv.get_hist(symbol=sym, exchange=EXCHANGE,
                             interval=interval, n_bars=n_bars)
        except Exception:
            continue
        if df is not None and len(df) > 200:
            return sym, df
    return None, None


def signals(df):
    ut = TI.ut_bot_alert(df['high'].values, df['low'].values,
                         df['close'].values,
                         key_value=KEY_VALUE, atr_period=ATR_PERIOD)
    lr = TI.linear_reg_candles(df['open'].values, df['high'].values,
                               df['low'].values, df['close'].values,
                               signal_length=SIGNAL_LENGTH, use_sma=USE_SMA,
                               linreg_length=LINREG_LENGTH)
    return ut, lr


def simulate(df, ut, lr, filtered, use_flip_exit):
    """Return closed trades. `filtered` applies the colour + line rules."""
    o, h, lo, c = (df['open'].values, df['high'].values,
                   df['low'].values, df['close'].values)
    idx = df.index
    buy, sell, tstop = ut['buy_signal'], ut['sell_signal'], ut['trailing_stop']
    line, lrc = lr['signal'], lr['close']
    green, red = lr['green_candles'], lr['red_candles']
    n = len(c)

    trades, i = [], 0
    while i < n - 1:
        is_buy, is_sell = bool(buy[i]), bool(sell[i])
        if not (is_buy or is_sell):
            i += 1
            continue

        if filtered:
            if np.isnan(line[i]) or np.isnan(lrc[i]):
                i += 1
                continue
            ok = ((is_buy and green[i] and lrc[i] > line[i]) or
                  (is_sell and red[i] and lrc[i] < line[i]))
            if not ok:
                i += 1
                continue

        entry, stop = o[i + 1], tstop[i]
        if np.isnan(entry) or np.isnan(stop):
            i += 1
            continue
        d = 1 if is_buy else -1
        risk = (entry - stop) * d
        if risk <= 0:
            i += 1
            continue
        target = entry + d * RR * risk

        outcome = exit_i = None
        for j in range(i + 1, n):
            hs = (lo[j] <= stop) if is_buy else (h[j] >= stop)
            ht = (h[j] >= target) if is_buy else (lo[j] <= target)
            if hs:
                outcome, exit_i = -1.0, j
                break
            if ht:
                outcome, exit_i = RR, j
                break
            if use_flip_exit and ((is_buy and sell[j]) or (is_sell and buy[j])):
                outcome, exit_i = d * (c[j] - entry) / risk, j
                break
        if outcome is None:
            break

        trades.append({
            'dir': 'BUY' if is_buy else 'SELL', 'R': outcome,
            'bars': exit_i - i, 'risk_pct': risk / entry * 100,
            'overnight': idx[exit_i].date() != idx[i + 1].date(),
        })
        i = exit_i + 1
    return trades


def summarise(trades):
    if not trades:
        return None
    R = np.array([t['R'] for t in trades])
    wins = R[R > 0]
    eq = np.cumsum(R)
    dd = (eq - np.maximum.accumulate(eq)).min()
    return {
        'n': len(R), 'win': (R > 0).mean() * 100, 'total': R.sum(),
        'exp': R.mean(), 'dd': dd,
        'avg_win': wins.mean() if len(wins) else 0.0,
        'avg_loss': R[R <= 0].mean() if (R <= 0).any() else 0.0,
        'bars': np.mean([t['bars'] for t in trades]),
        'risk_pct': np.median([t['risk_pct'] for t in trades]),
        'overnight': np.mean([t['overnight'] for t in trades]) * 100,
        'longs': sum(1 for t in trades if t['dir'] == 'BUY'),
        'long_R': sum(t['R'] for t in trades if t['dir'] == 'BUY'),
        'short_R': sum(t['R'] for t in trades if t['dir'] == 'SELL'),
    }


def show(label, s, tf_minutes):
    if s is None:
        print(f"  {label:<38} no trades")
        return
    be = 100 / (1 + RR)
    verdict = 'PROFIT' if s['total'] > 0 else 'LOSS'
    print(f"  {label:<38}{s['n']:>7}{s['win']:>7.1f}%"
          f"{s['total']:>+10.1f}{s['exp']:>+9.3f}{s['dd']:>+9.1f}  {verdict}")


def main():
    print("=" * 84)
    print("  NIFTY 50 INDEX - UT Bot + LinReg, last ~6 months")
    print("=" * 84)
    print(f"  Filter : BUY = signal + GREEN candle + close ABOVE line")
    print(f"           SELL = signal + RED candle + close BELOW line")
    print(f"  Params : key={KEY_VALUE} atr={ATR_PERIOD} signal={SIGNAL_LENGTH} "
          f"sma={USE_SMA} linreg={LINREG_LENGTH}   R:R = {RR}:1")

    tv = TvDatafeed()
    cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    collected = {}

    for name, interval, minutes, n_bars in TIMEFRAMES:
        sym, df = fetch(tv, interval, n_bars)
        if df is None:
            print(f"\n  {name}: could not fetch the index")
            continue
        df = df[df.index >= cutoff]
        if len(df) < 200:
            print(f"\n  {name}: too few bars in window ({len(df)})")
            continue

        ut, lr = signals(df)
        if ut is None or lr is None:
            continue

        print(f"\n{'=' * 84}")
        print(f"  {name}   symbol NSE:{sym}   {len(df)} bars   "
              f"{df.index[0].date()} -> {df.index[-1].date()}")
        print(f"{'=' * 84}")
        print(f"  {'variant':<38}{'trades':>7}{'win%':>8}"
              f"{'total R':>10}{'exp R':>9}{'maxDD':>9}")
        print(f"  {'-' * 80}")

        results = {}
        for filtered, flabel in [(False, 'no filter'), (True, 'FILTERED')]:
            for flip, elabel in [(True, 'exit on stop/target/flip'),
                                 (False, 'exit on stop/target only')]:
                tr = simulate(df, ut, lr, filtered, flip)
                s = summarise(tr)
                results[(filtered, flip)] = s
                show(f"{flabel} · {elabel}", s, minutes)

        s = results.get((True, True)) or results.get((True, False))
        if s:
            print(f"\n  Filtered-set detail:")
            print(f"    1R as % of price   : {s['risk_pct']:.3f}%   "
                  f"(0.05% costs = {0.05/s['risk_pct']:.2f} R/trade)")
            print(f"    avg win / loss     : {s['avg_win']:+.2f} R / "
                  f"{s['avg_loss']:+.2f} R")
            print(f"    avg hold           : {s['bars']:.1f} bars "
                  f"({s['bars']*minutes:.0f} min)")
            print(f"    held overnight     : {s['overnight']:.0f}% of trades")
            print(f"    longs / shorts     : {s['longs']} long "
                  f"({s['long_R']:+.1f} R) · {s['n']-s['longs']} short "
                  f"({s['short_R']:+.1f} R)")
            print(f"    break-even win rate at {RR}:1 is {100/(1+RR):.1f}%")
        collected[name] = results

    print(f"\n{'=' * 84}")
    print("  SUMMARY - filtered set, as specified (1.5:1, flip exit)")
    print(f"{'=' * 84}")
    print(f"  {'timeframe':<12}{'trades':>8}{'win%':>8}{'total R':>10}"
          f"{'exp R':>9}{'verdict':>12}")
    for name in collected:
        s = collected[name].get((True, True))
        if s:
            print(f"  {name:<12}{s['n']:>8}{s['win']:>7.1f}%{s['total']:>+10.1f}"
                  f"{s['exp']:>+9.3f}{'PROFIT' if s['total']>0 else 'LOSS':>12}")

    print(f"\n  Same, but WITHOUT the opposite-signal exit:")
    print(f"  {'timeframe':<12}{'trades':>8}{'win%':>8}{'total R':>10}"
          f"{'exp R':>9}{'verdict':>12}")
    for name in collected:
        s = collected[name].get((True, False))
        if s:
            print(f"  {name:<12}{s['n']:>8}{s['win']:>7.1f}%{s['total']:>+10.1f}"
                  f"{s['exp']:>+9.3f}{'PROFIT' if s['total']>0 else 'LOSS':>12}")


if __name__ == "__main__":
    main()
