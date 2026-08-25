"""
Intraday R:R sweep - the 2:1 target was inherited, not chosen
=============================================================

Intraday, only 7% of trades ever reach a 2R target; 73% are closed at the
session end. So the target barely participates. This sweeps R:R against
Key Value and ATR Period to find what actually suits a hold-until-close
profile.

Everything is validated on the pre-Feb-2026 portion, which the original
parameter search never saw.
"""

import logging
import pandas as pd
import numpy as np

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI
from intraday_test import trade_intraday, summary

OPT_START = pd.Timestamp('2026-02-24')
RRS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
KEYS = [1.0, 1.5, 2.0, 2.5, 3.0]
ATRS = [3, 5, 7, 10, 14, 21]
MIN_TRADES = 60


def main():
    tv = TvDatafeed()
    df = tv.get_hist(symbol='NIFTY', exchange='NSE',
                     interval=Interval.in_30_minute, n_bars=10000)
    idx = df.index
    o, h, lo, c = (df['open'].values, df['high'].values,
                   df['low'].values, df['close'].values)
    n = len(df)
    dates = np.array([d.date() for d in idx])
    lod = np.zeros(n, dtype=bool)
    lod[:-1] = dates[:-1] != dates[1:]
    lod[-1] = True
    split = int(np.searchsorted(idx, OPT_START))

    print("=" * 86)
    print("  INTRADAY R:R SWEEP - NIFTY 50 index, 30-min, flat at close")
    print(f"  {n} bars  {idx[0].date()} -> {idx[-1].date()}   "
          f"unseen portion = bars 0..{split}")
    print("=" * 86)

    rows = []
    for kv in KEYS:
        for ap in ATRS:
            u = TI.ut_bot_alert(h, lo, c, key_value=kv, atr_period=ap)
            b = u['buy_signal'].astype(bool)
            s = u['sell_signal'].astype(bool)
            t = u['trailing_stop']
            for rr in RRS:
                full = summary(trade_intraday(o, h, lo, c, b, s, t, lod,
                                              0, n, rr))
                un = summary(trade_intraday(o, h, lo, c, b, s, t, lod,
                                            0, split, rr))
                if full and full['n'] >= MIN_TRADES:
                    rows.append({'key': kv, 'atr': ap, 'rr': rr,
                                 'full': full, 'unseen': un})

    # --- R:R alone, averaged over the parameter grid -------------------
    print("\n  WHICH R:R WORKS BEST? (averaged over all key/atr combinations)")
    print(f"  {'R:R':>5}{'combos':>8}{'avg total R':>13}{'avg exp':>10}"
          f"{'% profitable':>14}{'avg unseen R':>14}")
    print("  " + "-" * 66)
    for rr in RRS:
        sub = [r for r in rows if r['rr'] == rr]
        if not sub:
            continue
        tot = np.mean([r['full']['total'] for r in sub])
        exp = np.mean([r['full']['exp'] for r in sub])
        prof = np.mean([r['full']['total'] > 0 for r in sub]) * 100
        uns = np.mean([r['unseen']['total'] for r in sub if r['unseen']])
        print(f"  {rr:>5.2f}{len(sub):>8}{tot:>+13.1f}{exp:>+10.3f}"
              f"{prof:>13.0f}%{uns:>+14.1f}")

    # --- best individual settings, ranked on the UNSEEN data -----------
    both = [r for r in rows if r['unseen'] and r['unseen']['n'] >= 40]
    both.sort(key=lambda r: -r['unseen']['total'])
    print(f"\n  TOP 15 RANKED ON UNSEEN DATA ONLY")
    print(f"  {'key':>5}{'atr':>5}{'R:R':>6}{'trades':>8}{'win%':>8}"
          f"{'full R':>9}{'unseen R':>10}{'exp':>9}{'maxDD':>8}{'PF':>7}")
    print("  " + "-" * 78)
    for r in both[:15]:
        f, u = r['full'], r['unseen']
        print(f"  {r['key']:>5.1f}{r['atr']:>5}{r['rr']:>6.2f}"
              f"{f['n']:>8}{f['win']:>7.1f}%{f['total']:>+9.1f}"
              f"{u['total']:>+10.1f}{f['exp']:>+9.3f}{f['dd']:>8.1f}"
              f"{f['pf']:>7.2f}")

    pos_both = [r for r in both
                if r['full']['total'] > 0 and r['unseen']['total'] > 0]
    print(f"\n  Profitable in BOTH full and unseen: {len(pos_both)}/{len(both)}"
          f"  ({len(pos_both)/max(len(both),1)*100:.0f}%)")

    best = both[0]
    print(f"\n  => Best on unseen data: key={best['key']} atr={best['atr']} "
          f"R:R={best['rr']}")
    print(f"     full {best['full']['total']:+.1f} R over {best['full']['n']} "
          f"trades ({best['full']['win']:.1f}% win, PF {best['full']['pf']:.2f})")
    print(f"     unseen {best['unseen']['total']:+.1f} R over "
          f"{best['unseen']['n']} trades")
    return best


if __name__ == "__main__":
    main()
