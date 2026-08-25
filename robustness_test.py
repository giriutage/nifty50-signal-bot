"""
Regime robustness - NIFTY 50 index, 30-min, over the full available history
===========================================================================

tvDatafeed serves ~5,300 bars of NIFTY 30-min, reaching back to Jan 2025.
The parameter search only ever saw the last six months (from 2026-02-24),
so everything before that date is TRUE out-of-sample: never inspected, never
used for selection.

That makes this the real test. A setting that was curve-fitted to Feb-Aug
2026 has no reason to work in 2025.

All figures use REALISTIC fills: when a bar gaps through the stop, the fill
is the bar's open, not the stop price. That is what actually happens, and it
matters here because most trades are held overnight.
"""

import logging
from datetime import datetime

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

import numpy as np
import pandas as pd
from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI

KEY, ATR, RR = 1.5, 10, 2.0
OPT_START = pd.Timestamp('2026-02-24')      # the window used for selection
KEY_VALUES = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
ATR_PERIODS = [1, 3, 5, 7, 10, 14, 21]


def trade(o, h, lo, c, buy, sell, ts, lo_i, hi_i, rr=RR):
    """Closed trades in [lo_i, hi_i) with realistic gap fills."""
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
        for j in range(i + 1, hi_i):
            hs = (lo[j] <= stop) if buy[i] else (h[j] >= stop)
            ht = (h[j] >= target) if buy[i] else (lo[j] <= target)
            if hs:
                beyond = (o[j] < stop) if buy[i] else (o[j] > stop)
                fill = o[j] if beyond else stop
                r, ex = d * (fill - entry) / risk, j
                break
            if ht:
                r, ex = rr, j
                break
        if r is None:
            break
        out.append({'R': r, 'i': i + 1, 'j': ex,
                    'dir': 'BUY' if buy[i] else 'SELL'})
        i = ex + 1
    return out


def summary(trades):
    if not trades:
        return None
    R = np.array([t['R'] for t in trades])
    eq = np.cumsum(R)
    w = R[R > 0]
    l = R[R <= 0]
    return {'n': len(R), 'win': (R > 0).mean() * 100, 'total': R.sum(),
            'exp': R.mean(), 'dd': (eq - np.maximum.accumulate(eq)).min(),
            'pf': (w.sum() / abs(l.sum())) if len(l) and l.sum() else float('inf')}


def line(label, s):
    if s is None:
        print(f"  {label:<34}      no trades")
        return
    verdict = 'PROFIT' if s['total'] > 0 else 'LOSS'
    print(f"  {label:<34}{s['n']:>7}{s['win']:>7.1f}%{s['total']:>+9.1f}"
          f"{s['exp']:>+9.3f}{s['dd']:>+8.1f}{s['pf']:>7.2f}  {verdict}")


def main():
    tv = TvDatafeed()
    df = tv.get_hist(symbol='NIFTY', exchange='NSE',
                     interval=Interval.in_30_minute, n_bars=10000)
    idx = df.index
    o, h, lo, c = (df['open'].values, df['high'].values,
                   df['low'].values, df['close'].values)
    n = len(df)

    print("=" * 88)
    print("  REGIME ROBUSTNESS - NIFTY 50 index, 30-min")
    print(f"  Key Value {KEY} · ATR Period {ATR} · no filter · "
          f"stop/target only · R:R {RR}:1")
    print(f"  Realistic gap fills applied throughout")
    print("=" * 88)
    print(f"  History: {n} bars   {idx[0].date()} -> {idx[-1].date()}  "
          f"(~{(idx[-1]-idx[0]).days/365:.2f} years)")

    split = int(np.searchsorted(idx, OPT_START))
    print(f"  Selection window began {OPT_START.date()} (bar {split})")
    print(f"  => bars 0..{split} are TRUE out-of-sample: never seen "
          f"during optimisation\n")

    ut = TI.ut_bot_alert(h, lo, c, key_value=KEY, atr_period=ATR)
    buy = ut['buy_signal'].astype(bool)
    sell = ut['sell_signal'].astype(bool)
    ts = ut['trailing_stop']

    hdr = (f"  {'period':<34}{'trades':>7}{'win%':>8}{'total':>9}"
           f"{'exp':>9}{'maxDD':>8}{'PF':>7}")
    print(hdr)
    print("  " + "-" * 84)
    full = summary(trade(o, h, lo, c, buy, sell, ts, 0, n))
    unseen = summary(trade(o, h, lo, c, buy, sell, ts, 0, split))
    seen = summary(trade(o, h, lo, c, buy, sell, ts, split, n))
    line("FULL HISTORY", full)
    line("TRUE out-of-sample (pre Feb-26)", unseen)
    line("selection window (Feb-Aug 26)", seen)

    # ---- calendar quarters -------------------------------------------
    print(f"\n  By quarter:")
    print(hdr)
    print("  " + "-" * 84)
    all_tr = trade(o, h, lo, c, buy, sell, ts, 0, n)
    q = {}
    for t in all_tr:
        k = f"{idx[t['j']].year} Q{(idx[t['j']].month - 1)//3 + 1}"
        q.setdefault(k, []).append(t)
    pos = 0
    for k in sorted(q):
        s = summary(q[k])
        line(k, s)
        if s and s['total'] > 0:
            pos += 1
    print(f"\n  {pos}/{len(q)} quarters positive")

    # ---- monthly equity ----------------------------------------------
    R = np.array([t['R'] for t in all_tr])
    boot = [np.random.choice(R, len(R), replace=True).sum() for _ in range(5000)]
    p = (R > 0).mean()
    se = np.sqrt(p * (1 - p) / len(R))
    print(f"\n  Win rate {p*100:.1f}%  95% CI "
          f"[{(p-1.96*se)*100:.1f}%, {(p+1.96*se)*100:.1f}%]   "
          f"break-even {100/(1+RR):.1f}%")
    print(f"  Bootstrap total R: median {np.median(boot):+.1f}  "
          f"5th pct {np.percentile(boot,5):+.1f}  "
          f"P(profit) = {np.mean(np.array(boot)>0)*100:.1f}%")

    # ---- does the profitable REGION persist over the long window? -----
    print(f"\n{'=' * 88}")
    print("  PARAMETER MAP OVER THE FULL 1.65 YEARS (total R, realistic fills)")
    print(f"{'=' * 88}")
    print("       atr" + "".join(f"{a:>9}" for a in ATR_PERIODS))
    grid = {}
    for kv in KEY_VALUES:
        cells = []
        for ap in ATR_PERIODS:
            u = TI.ut_bot_alert(h, lo, c, key_value=kv, atr_period=ap)
            b2, s2, t2 = (u['buy_signal'].astype(bool),
                          u['sell_signal'].astype(bool), u['trailing_stop'])
            sfull = summary(trade(o, h, lo, c, b2, s2, t2, 0, n))
            sun = summary(trade(o, h, lo, c, b2, s2, t2, 0, split))
            grid[(kv, ap)] = (sfull, sun)
            cells.append("        ." if not sfull else f"{sfull['total']:>+9.1f}")
        print(f"  key {kv:<5}" + "".join(cells))

    print(f"\n  Same map, TRUE out-of-sample portion only (pre Feb-26)")
    print("       atr" + "".join(f"{a:>9}" for a in ATR_PERIODS))
    for kv in KEY_VALUES:
        cells = []
        for ap in ATR_PERIODS:
            s = grid[(kv, ap)][1]
            cells.append("        ." if not s else f"{s['total']:>+9.1f}")
        print(f"  key {kv:<5}" + "".join(cells))

    ok = [(k, a) for (k, a), (f, u) in grid.items()
          if f and u and f['total'] > 0 and u['total'] > 0]
    print(f"\n  Cells profitable over BOTH the full period and the unseen "
          f"portion: {len(ok)}/{len(grid)}")
    if ok:
        print("   ", ", ".join(f"k{k}/a{a}" for k, a in sorted(ok)))


if __name__ == "__main__":
    main()
