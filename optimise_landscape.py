"""
Parameter landscape - is there a stable REGION, or isolated lucky cells?

A real effect shows up as a contiguous block of profitable parameter
combinations that holds in both halves of the data. Curve-fitting shows up
as scattered spikes surrounded by losses. This prints the map so the
difference is visible rather than argued.

Also reports 1R as a percentage of price per cell, since a tight stop makes
any edge unrecoverable after costs no matter how good the R figure looks.
"""

import logging
from datetime import datetime, timedelta

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

import numpy as np
from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI
from optimise_index import (simulate, stats, RR, KEY_VALUES, ATR_PERIODS,
                            LOOKBACK_DAYS, INDEX_CANDIDATES)

TIMEFRAMES = [('15-min', Interval.in_15_minute, 6000),
              ('30-min', Interval.in_30_minute, 4000)]

COST_PCT = 0.05          # assumed round-trip cost, % of turnover


def risk_profile(o, buy, sell, tstop):
    """Median 1R as a % of entry price."""
    vals = []
    for i in range(len(o) - 1):
        if not (buy[i] or sell[i]):
            continue
        e = o[i + 1]
        r = (e - tstop[i]) * (1 if buy[i] else -1)
        if r > 0 and e > 0:
            vals.append(r / e * 100)
    return float(np.median(vals)) if vals else float('nan')


def main():
    tv = TvDatafeed()
    cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)

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
        if df is None:
            continue
        df = df[df.index >= cutoff]
        n = len(df)
        split = n // 2
        o, h, lo, c = (df['open'].values, df['high'].values,
                       df['low'].values, df['close'].values)
        dummy = np.zeros(n)
        dummy_b = np.zeros(n, dtype=bool)

        print("=" * 92)
        print(f"  {tf_name}   no filter · exit on stop/target only · R:R {RR}:1")
        print(f"  {n} bars   {df.index[0].date()} -> {df.index[-1].date()}")
        print("=" * 92)

        grid = {}
        for kv in KEY_VALUES:
            for ap in ATR_PERIODS:
                u = TI.ut_bot_alert(h, lo, c, key_value=kv, atr_period=ap)
                if u is None:
                    continue
                buy = u['buy_signal'].astype(bool)
                sell = u['sell_signal'].astype(bool)
                ts = u['trailing_stop']
                args = (o, h, lo, c, buy, sell, ts,
                        dummy, dummy, dummy_b, dummy_b, False, False)
                f = stats(simulate(*args, 0, n))
                a = stats(simulate(*args, 0, split))
                b = stats(simulate(*args, split, n))
                grid[(kv, ap)] = (f, a, b, risk_profile(o, buy, sell, ts))

        def table(title, pick):
            print(f"\n  {title}")
            print("       atr" + "".join(f"{ap:>9}" for ap in ATR_PERIODS))
            for kv in KEY_VALUES:
                cells = []
                for ap in ATR_PERIODS:
                    g = grid.get((kv, ap))
                    v = pick(g)
                    cells.append("        ." if v is None else f"{v:>+9.1f}")
                print(f"  key {kv:<5}" + "".join(cells))

        table("FULL PERIOD total R", lambda g: g[0]['total'] if g and g[0] else None)
        table("FIRST half total R", lambda g: g[1]['total'] if g and g[1] else None)
        table("SECOND half total R", lambda g: g[2]['total'] if g and g[2] else None)

        print(f"\n  1R as % of price (median)")
        print("       atr" + "".join(f"{ap:>9}" for ap in ATR_PERIODS))
        for kv in KEY_VALUES:
            cells = []
            for ap in ATR_PERIODS:
                g = grid.get((kv, ap))
                cells.append("        ." if not g or np.isnan(g[3])
                             else f"{g[3]:>9.3f}")
            print(f"  key {kv:<5}" + "".join(cells))

        both = [(kv, ap) for (kv, ap), g in grid.items()
                if g[1] and g[2] and g[1]['total'] > 0 and g[2]['total'] > 0]
        total_cells = len([1 for g in grid.values() if g[1] and g[2]])
        print(f"\n  Cells positive in BOTH halves: {len(both)} / {total_cells}")
        if both:
            print("   ", ", ".join(f"key{k}/atr{a}" for k, a in sorted(both)))

        # cost check on the best full-period cell
        best = max((g for g in grid.values() if g[0]),
                   key=lambda g: g[0]['total'], default=None)
        if best:
            bk = [k for k, g in grid.items() if g is best][0]
            f, a, b, rp = best
            cost_R = COST_PCT / rp if rp and not np.isnan(rp) else float('nan')
            print(f"\n  Best full-period cell: key={bk[0]} atr={bk[1]}")
            print(f"    {f['n']} trades · {f['win']:.1f}% win · "
                  f"{f['total']:+.1f} R · {f['exp']:+.3f} R/trade")
            print(f"    halves: {a['total']:+.1f} R  /  {b['total']:+.1f} R")
            print(f"    1R = {rp:.3f}% of price -> a {COST_PCT}% round trip "
                  f"costs {cost_R:.3f} R per trade")
            print(f"    net of that cost: {f['exp'] - cost_R:+.3f} R/trade "
                  f"-> {'still positive' if f['exp'] > cost_R else 'NEGATIVE'}")
        print()


if __name__ == "__main__":
    main()
