"""
Intraday-only variant - does the edge survive being forced flat at the close?
============================================================================

The swing version holds 82% of trades overnight, which carries gap risk and
requires full margin. This forces every position closed at the last bar of
its own session and asks whether anything is left.

Two things could break it:
  * a 2R target may simply be unreachable inside one session, so winners get
    cut at whatever the close happens to be;
  * the losers still get their full stop, so the payoff can turn asymmetric
    in the wrong direction.

Realistic gap fills are irrelevant here - no position survives the night -
which is precisely the point of testing it.
"""

import logging
from datetime import datetime

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

import numpy as np
import pandas as pd
from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI

RR = 2.0
OPT_START = pd.Timestamp('2026-02-24')
KEY_VALUES = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
ATR_PERIODS = [1, 3, 5, 7, 10, 14, 21]
BEST = (1.5, 10)


def trade_intraday(o, h, lo, c, buy, sell, ts, last_of_day, lo_i, hi_i, rr=RR):
    """Positions are closed at the final bar of their own session."""
    out, i = [], lo_i
    while i < hi_i - 1:
        if not (buy[i] or sell[i]):
            i += 1
            continue
        entry, stop = o[i + 1], ts[i]
        if np.isnan(entry) or np.isnan(stop):
            i += 1
            continue
        d = 1.0 if buy[i] else -1.0
        risk = (entry - stop) * d
        if risk <= 0:
            i += 1
            continue
        target = entry + d * rr * risk

        r = ex = None
        how = None
        for j in range(i + 1, hi_i):
            hs = (lo[j] <= stop) if buy[i] else (h[j] >= stop)
            ht = (h[j] >= target) if buy[i] else (lo[j] <= target)
            if hs:
                r, ex, how = -1.0, j, 'stop'
                break
            if ht:
                r, ex, how = rr, j, 'target'
                break
            if last_of_day[j]:                 # forced flat on the close
                r, ex, how = d * (c[j] - entry) / risk, j, 'eod'
                break
        if r is None:
            break
        out.append({'R': r, 'how': how, 'bars': ex - i,
                    'i': i + 1, 'j': ex,
                    'dir': 'BUY' if buy[i] else 'SELL'})
        i = ex + 1
    return out


def summary(tr):
    if not tr:
        return None
    R = np.array([t['R'] for t in tr])
    eq = np.cumsum(R)
    w, l = R[R > 0], R[R <= 0]
    return {'n': len(R), 'win': (R > 0).mean() * 100, 'total': R.sum(),
            'exp': R.mean(), 'dd': (eq - np.maximum.accumulate(eq)).min(),
            'pf': (w.sum() / abs(l.sum())) if len(l) and l.sum() else float('inf')}


def line(label, s):
    if s is None:
        print(f"  {label:<34}      no trades")
        return
    print(f"  {label:<34}{s['n']:>7}{s['win']:>7.1f}%{s['total']:>+9.1f}"
          f"{s['exp']:>+9.3f}{s['dd']:>8.1f}{s['pf']:>7.2f}  "
          f"{'PROFIT' if s['total'] > 0 else 'LOSS'}")


def main():
    tv = TvDatafeed()
    df = tv.get_hist(symbol='NIFTY', exchange='NSE',
                     interval=Interval.in_30_minute, n_bars=10000)
    idx = df.index
    o, h, lo, c = (df['open'].values, df['high'].values,
                   df['low'].values, df['close'].values)
    n = len(df)

    # mark the final bar of each trading session
    dates = np.array([d.date() for d in idx])
    last_of_day = np.zeros(n, dtype=bool)
    last_of_day[:-1] = dates[:-1] != dates[1:]
    last_of_day[-1] = True

    split = int(np.searchsorted(idx, OPT_START))

    print("=" * 88)
    print("  INTRADAY-ONLY - NIFTY 50 index, 30-min, flat at every close")
    print(f"  R:R {RR}:1 · no filter · stop / target / end-of-day")
    print("=" * 88)
    print(f"  History: {n} bars   {idx[0].date()} -> {idx[-1].date()}")
    print(f"  Sessions: {len(set(dates))}   "
          f"avg {n/len(set(dates)):.1f} bars/session\n")

    kv, ap = BEST
    ut = TI.ut_bot_alert(h, lo, c, key_value=kv, atr_period=ap)
    buy = ut['buy_signal'].astype(bool)
    sell = ut['sell_signal'].astype(bool)
    ts = ut['trailing_stop']

    hdr = (f"  {'period':<34}{'trades':>7}{'win%':>8}{'total':>9}"
           f"{'exp':>9}{'maxDD':>8}{'PF':>7}")
    print(f"  Best swing setting (key {kv} / atr {ap}) run intraday-only:")
    print(hdr)
    print("  " + "-" * 84)
    all_tr = trade_intraday(o, h, lo, c, buy, sell, ts, last_of_day, 0, n)
    line("FULL HISTORY", summary(all_tr))
    line("TRUE out-of-sample (pre Feb-26)",
         summary(trade_intraday(o, h, lo, c, buy, sell, ts, last_of_day, 0, split)))
    line("selection window (Feb-Aug 26)",
         summary(trade_intraday(o, h, lo, c, buy, sell, ts, last_of_day, split, n)))

    if all_tr:
        from collections import Counter
        cnt = Counter(t['how'] for t in all_tr)
        print(f"\n  How trades ended:")
        for k in ('target', 'stop', 'eod'):
            v = cnt.get(k, 0)
            rs = [t['R'] for t in all_tr if t['how'] == k]
            print(f"    {k:<8}{v:>5}  ({v/len(all_tr)*100:>4.0f}%)   "
                  f"avg {np.mean(rs):+.2f} R" if rs else f"    {k:<8}{v:>5}")
        print(f"  median hold: {np.median([t['bars'] for t in all_tr]):.0f} bars "
              f"({np.median([t['bars'] for t in all_tr])*30/60:.1f} h)")

    # ---- sweep: intraday may prefer different parameters ---------------
    print(f"\n{'=' * 88}")
    print("  PARAMETER MAP - INTRADAY ONLY, full history (total R)")
    print(f"{'=' * 88}")
    print("       atr" + "".join(f"{a:>9}" for a in ATR_PERIODS))
    grid = {}
    for k in KEY_VALUES:
        cells = []
        for a in ATR_PERIODS:
            u = TI.ut_bot_alert(h, lo, c, key_value=k, atr_period=a)
            b2, s2, t2 = (u['buy_signal'].astype(bool),
                          u['sell_signal'].astype(bool), u['trailing_stop'])
            sf = summary(trade_intraday(o, h, lo, c, b2, s2, t2, last_of_day, 0, n))
            su = summary(trade_intraday(o, h, lo, c, b2, s2, t2, last_of_day, 0, split))
            grid[(k, a)] = (sf, su)
            cells.append("        ." if not sf else f"{sf['total']:>+9.1f}")
        print(f"  key {k:<5}" + "".join(cells))

    print(f"\n  Same, TRUE out-of-sample only (pre Feb-26)")
    print("       atr" + "".join(f"{a:>9}" for a in ATR_PERIODS))
    for k in KEY_VALUES:
        cells = []
        for a in ATR_PERIODS:
            s = grid[(k, a)][1]
            cells.append("        ." if not s else f"{s['total']:>+9.1f}")
        print(f"  key {k:<5}" + "".join(cells))

    ok = [(k, a) for (k, a), (f, u) in grid.items()
          if f and u and f['total'] > 0 and u['total'] > 0]
    print(f"\n  Profitable in BOTH full and unseen: {len(ok)}/{len(grid)}")
    if ok:
        print("   ", ", ".join(f"k{k}/a{a}" for k, a in sorted(ok)))
    best = max((x for x in grid.items() if x[1][0]),
               key=lambda x: x[1][0]['total'], default=None)
    if best:
        (k, a), (f, u) = best
        print(f"\n  Best intraday cell: key={k} atr={a}   "
              f"full {f['total']:+.1f} R ({f['n']} trades, {f['win']:.1f}% win)"
              f"   unseen {u['total']:+.1f} R" if u else "")


if __name__ == "__main__":
    main()
