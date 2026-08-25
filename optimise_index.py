"""
Parameter search - NIFTY 50 index, UT Bot + LinReg, R:R fixed at 2:1
====================================================================

Searches Key Value x ATR Period x Signal Smoothing x LinReg Length across
5 / 15 / 30-minute bars, with and without the colour+line filter, and with
and without the opposite-signal exit.

Guarding against fooling ourselves
----------------------------------
Thousands of combinations over one instrument and six months will throw up
winners by chance alone. Two defences:

* OUT-OF-SAMPLE SPLIT. The series is halved. Parameters are ranked on the
  first half (in-sample); the second half (out-of-sample) is never used for
  selection, only for checking. A setting that works in-sample and collapses
  out-of-sample was curve-fitted.
* BREADTH. If only a handful of combinations out of thousands are
  profitable, that is what randomness looks like. If a broad contiguous
  region is profitable, that is closer to a real effect.

Indicators are computed once per parameter pair and reused, so the cost is
in the trade simulations rather than the maths.
"""

import logging
import itertools
from datetime import datetime, timedelta

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

import numpy as np
from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI

# ---------------------------------------------------------------- config

RR = 2.0
MIN_TRADES = 30          # below this a result is not evidence of anything

KEY_VALUES = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
ATR_PERIODS = [1, 3, 5, 7, 10, 14, 21]
SIGNAL_LENGTHS = [5, 7, 9, 12]
LINREG_LENGTHS = [8, 11, 14, 20]

TIMEFRAMES = [
    ('5-min',  Interval.in_5_minute,  12000),
    ('15-min', Interval.in_15_minute, 6000),
    ('30-min', Interval.in_30_minute, 4000),
]

LOOKBACK_DAYS = 183
INDEX_CANDIDATES = ['NIFTY', 'NIFTY50', 'CNXNIFTY']


def simulate(o, h, lo, c, buy, sell, tstop, line, lrc, green, red,
             filtered, flip, lo_i, hi_i):
    """Trade R-multiples for bars in [lo_i, hi_i)."""
    out = []
    i = lo_i
    while i < hi_i - 1:
        is_buy, is_sell = buy[i], sell[i]
        if not (is_buy or is_sell):
            i += 1
            continue

        if filtered:
            if np.isnan(line[i]) or np.isnan(lrc[i]):
                i += 1
                continue
            if not ((is_buy and green[i] and lrc[i] > line[i]) or
                    (is_sell and red[i] and lrc[i] < line[i])):
                i += 1
                continue

        entry, stop = o[i + 1], tstop[i]
        if np.isnan(entry) or np.isnan(stop):
            i += 1
            continue
        d = 1.0 if is_buy else -1.0
        risk = (entry - stop) * d
        if risk <= 0:
            i += 1
            continue
        target = entry + d * RR * risk

        outcome = exit_i = None
        for j in range(i + 1, hi_i):
            if is_buy:
                hs, ht = lo[j] <= stop, h[j] >= target
            else:
                hs, ht = h[j] >= stop, lo[j] <= target
            if hs:
                outcome, exit_i = -1.0, j
                break
            if ht:
                outcome, exit_i = RR, j
                break
            if flip and ((is_buy and sell[j]) or (is_sell and buy[j])):
                outcome, exit_i = d * (c[j] - entry) / risk, j
                break
        if outcome is None:
            break
        out.append(outcome)
        i = exit_i + 1
    return out


def stats(R):
    if not R:
        return None
    a = np.array(R)
    eq = np.cumsum(a)
    return {'n': len(a), 'win': (a > 0).mean() * 100, 'total': a.sum(),
            'exp': a.mean(), 'dd': (eq - np.maximum.accumulate(eq)).min()}


def main():
    print("=" * 88)
    print(f"  PARAMETER SEARCH - NIFTY 50 index   R:R fixed at {RR}:1")
    print("=" * 88)
    combos = (len(KEY_VALUES) * len(ATR_PERIODS) *
              (1 + len(SIGNAL_LENGTHS) * len(LINREG_LENGTHS)) * 2)
    print(f"  Grid: key {KEY_VALUES}")
    print(f"        atr {ATR_PERIODS}")
    print(f"        signal {SIGNAL_LENGTHS}   linreg {LINREG_LENGTHS}")
    print(f"        x filter on/off  x flip-exit on/off")
    print(f"  ~{combos} combinations per timeframe\n")

    tv = TvDatafeed()
    cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    everything = []

    for tf_name, interval, n_bars in TIMEFRAMES:
        df = None
        for sym in INDEX_CANDIDATES:
            try:
                df = tv.get_hist(symbol=sym, exchange='NSE',
                                 interval=interval, n_bars=n_bars)
            except Exception:
                continue
            if df is not None and len(df) > 200:
                break
        if df is None or len(df) < 200:
            print(f"  {tf_name}: no data")
            continue
        df = df[df.index >= cutoff]
        n = len(df)
        split = n // 2

        o, h, lo, c = (df['open'].values, df['high'].values,
                       df['low'].values, df['close'].values)

        print(f"  {tf_name}: {n} bars  {df.index[0].date()} -> {df.index[-1].date()}"
              f"   IS/OOS split at bar {split} ({df.index[split].date()})")

        # indicators cached per parameter pair
        ut_cache, lr_cache = {}, {}
        for kv, ap in itertools.product(KEY_VALUES, ATR_PERIODS):
            u = TI.ut_bot_alert(h, lo, c, key_value=kv, atr_period=ap)
            if u is not None:
                ut_cache[(kv, ap)] = (u['buy_signal'].astype(bool),
                                      u['sell_signal'].astype(bool),
                                      u['trailing_stop'])
        for sl, ll in itertools.product(SIGNAL_LENGTHS, LINREG_LENGTHS):
            g = TI.linear_reg_candles(o, h, lo, c, signal_length=sl,
                                      use_sma=True, linreg_length=ll)
            if g is not None:
                lr_cache[(sl, ll)] = (g['signal'], g['close'],
                                      g['green_candles'], g['red_candles'])

        dummy = np.zeros(n)
        dummy_b = np.zeros(n, dtype=bool)

        for (kv, ap), (buy, sell, tstop) in ut_cache.items():
            for flip in (True, False):
                # ---- unfiltered: LinReg parameters are irrelevant here
                full = simulate(o, h, lo, c, buy, sell, tstop,
                                dummy, dummy, dummy_b, dummy_b,
                                False, flip, 0, n)
                is_ = simulate(o, h, lo, c, buy, sell, tstop,
                               dummy, dummy, dummy_b, dummy_b,
                               False, flip, 0, split)
                oos = simulate(o, h, lo, c, buy, sell, tstop,
                               dummy, dummy, dummy_b, dummy_b,
                               False, flip, split, n)
                s, si, so = stats(full), stats(is_), stats(oos)
                if s and s['n'] >= MIN_TRADES:
                    everything.append({
                        'tf': tf_name, 'key': kv, 'atr': ap,
                        'sig': None, 'lin': None, 'filt': False, 'flip': flip,
                        'full': s, 'is': si, 'oos': so})

                # ---- filtered
                for (sl, ll), (line, lrc, green, red) in lr_cache.items():
                    full = simulate(o, h, lo, c, buy, sell, tstop,
                                    line, lrc, green, red, True, flip, 0, n)
                    is_ = simulate(o, h, lo, c, buy, sell, tstop,
                                   line, lrc, green, red, True, flip, 0, split)
                    oos = simulate(o, h, lo, c, buy, sell, tstop,
                                   line, lrc, green, red, True, flip, split, n)
                    s, si, so = stats(full), stats(is_), stats(oos)
                    if s and s['n'] >= MIN_TRADES:
                        everything.append({
                            'tf': tf_name, 'key': kv, 'atr': ap,
                            'sig': sl, 'lin': ll, 'filt': True, 'flip': flip,
                            'full': s, 'is': si, 'oos': so})
        print(f"     -> {sum(1 for e in everything if e['tf']==tf_name)} "
              f"combinations with >= {MIN_TRADES} trades")

    if not everything:
        print("\n  Nothing met the minimum trade count.")
        return

    tested = len(everything)
    profitable = [e for e in everything if e['full']['total'] > 0]
    print(f"\n{'=' * 88}")
    print("  BREADTH")
    print(f"{'=' * 88}")
    print(f"  Combinations with >= {MIN_TRADES} trades : {tested}")
    print(f"  Profitable over the full period       : {len(profitable)}"
          f"  ({len(profitable)/tested*100:.1f}%)")
    print(f"  If results were random we would expect roughly half.")

    def row(e):
        f, i_, o_ = e['full'], e['is'], e['oos']
        filt = (f"S{e['sig']}/L{e['lin']}" if e['filt'] else "off")
        return (f"  {e['tf']:<7}{e['key']:>5.1f}{e['atr']:>5}"
                f"{filt:>9}{'flip' if e['flip'] else 'st/tg':>7}"
                f"{f['n']:>7}{f['win']:>7.1f}%{f['total']:>+9.1f}"
                f"{(i_['total'] if i_ else 0):>+9.1f}"
                f"{(o_['total'] if o_ else 0):>+9.1f}")

    hdr = (f"  {'tf':<7}{'key':>5}{'atr':>5}{'filter':>9}{'exit':>7}"
           f"{'trades':>7}{'win%':>8}{'total':>9}{'IS':>9}{'OOS':>9}")

    print(f"\n{'=' * 88}")
    print("  TOP 12 BY FULL-PERIOD TOTAL R  (the naive answer - likely overfit)")
    print(f"{'=' * 88}")
    print(hdr)
    for e in sorted(everything, key=lambda x: -x['full']['total'])[:12]:
        print(row(e))

    # honest selection: rank on in-sample, judge on out-of-sample
    with_both = [e for e in everything
                 if e['is'] and e['oos']
                 and e['is']['n'] >= 15 and e['oos']['n'] >= 15]
    print(f"\n{'=' * 88}")
    print("  SELECTED ON IN-SAMPLE ONLY, THEN CHECKED OUT-OF-SAMPLE")
    print(f"{'=' * 88}")
    print(hdr)
    top_is = sorted(with_both, key=lambda x: -x['is']['total'])[:12]
    for e in top_is:
        print(row(e))

    held = [e for e in top_is if e['oos']['total'] > 0]
    print(f"\n  Of the top 12 in-sample, {len(held)} stayed profitable "
          f"out-of-sample.")
    if held:
        print("  Survivors:")
        for e in held:
            print(row(e))
    else:
        print("  None survived. That is the signature of curve-fitting:")
        print("  the in-sample winners carry no information about the future.")

    both_pos = [e for e in with_both
                if e['is']['total'] > 0 and e['oos']['total'] > 0]
    print(f"\n  Profitable in BOTH halves: {len(both_pos)} of {len(with_both)}"
          f"  ({len(both_pos)/max(len(with_both),1)*100:.1f}%)")
    if both_pos:
        print(f"\n{'=' * 88}")
        print("  PROFITABLE IN BOTH HALVES - ranked by the weaker half")
        print(f"{'=' * 88}")
        print(hdr)
        for e in sorted(both_pos,
                        key=lambda x: -min(x['is']['total'], x['oos']['total']))[:12]:
            print(row(e))


if __name__ == "__main__":
    main()
