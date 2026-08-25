"""
Full detail on the candidate that came out of the parameter search.

  NIFTY 50 index · 30-min · Key Value 1.5 · ATR Period 10
  no colour/line filter · exit on stop or target only · R:R 2:1

Chosen not because it was the single highest number, but because its whole
neighbourhood is profitable (atr 5/7/10/14/21 all between +19 and +37 R),
which is what separates a real effect from a lucky cell.
"""

import logging
from datetime import datetime, timedelta

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

import numpy as np
import pandas as pd
from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI

KEY_VALUE, ATR_PERIOD, RR = 1.5, 10, 2.0
COSTS = (0.03, 0.05, 0.10)


def main():
    tv = TvDatafeed()
    df = tv.get_hist(symbol='NIFTY', exchange='NSE',
                     interval=Interval.in_30_minute, n_bars=4000)
    df = df[df.index >= datetime.now() - timedelta(days=183)]
    o, h, lo, c = (df['open'].values, df['high'].values,
                   df['low'].values, df['close'].values)
    idx = df.index
    n = len(df)

    ut = TI.ut_bot_alert(h, lo, c, key_value=KEY_VALUE, atr_period=ATR_PERIOD)
    buy, sell, tstop = (ut['buy_signal'].astype(bool),
                        ut['sell_signal'].astype(bool), ut['trailing_stop'])

    trades, i = [], 0
    while i < n - 1:
        if not (buy[i] or sell[i]):
            i += 1
            continue
        entry, stop = o[i + 1], tstop[i]
        if np.isnan(entry) or np.isnan(stop):
            i += 1
            continue
        d = 1.0 if buy[i] else -1.0
        risk = (entry - stop) * d
        if risk <= 0:
            i += 1
            continue
        target = entry + d * RR * risk
        outcome = exit_i = None
        for j in range(i + 1, n):
            hs = (lo[j] <= stop) if buy[i] else (h[j] >= stop)
            ht = (h[j] >= target) if buy[i] else (lo[j] <= target)
            if hs:
                outcome, exit_i = -1.0, j
                break
            if ht:
                outcome, exit_i = RR, j
                break
        if outcome is None:
            break
        trades.append({'dir': 'BUY' if buy[i] else 'SELL', 'R': outcome,
                       'entry_t': idx[i + 1], 'exit_t': idx[exit_i],
                       'bars': exit_i - i, 'risk_pct': risk / entry * 100,
                       'overnight': idx[exit_i].date() != idx[i + 1].date(),
                       'days': (idx[exit_i].date() - idx[i + 1].date()).days})
        i = exit_i + 1

    R = np.array([t['R'] for t in trades])
    eq = np.cumsum(R)
    dd = eq - np.maximum.accumulate(eq)
    wins, losses = R[R > 0], R[R <= 0]
    med_risk = np.median([t['risk_pct'] for t in trades])

    print("=" * 78)
    print("  VALIDATION - NIFTY 50 index, 30-min")
    print(f"  Key Value {KEY_VALUE} · ATR Period {ATR_PERIOD} · "
          f"no filter · stop/target only · R:R {RR}:1")
    print("=" * 78)
    print(f"  Window            : {idx[0].date()} -> {idx[-1].date()}  "
          f"({n} bars)")
    print(f"  Trades            : {len(R)}   "
          f"({sum(1 for t in trades if t['dir']=='BUY')} long / "
          f"{sum(1 for t in trades if t['dir']=='SELL')} short)")
    print(f"  Win rate          : {(R>0).mean()*100:.1f}%   "
          f"(break-even at {RR}:1 is {100/(1+RR):.1f}%)")
    print(f"  Total             : {R.sum():+.1f} R")
    print(f"  Expectancy        : {R.mean():+.3f} R/trade")
    print(f"  Max drawdown      : {dd.min():+.1f} R")
    print(f"  Avg win / loss    : {wins.mean():+.2f} / {losses.mean():+.2f} R")
    print(f"  Profit factor     : {wins.sum()/abs(losses.sum()):.2f}")

    print(f"\n  1R                : {med_risk:.3f}% of price (median)")
    print(f"  Net of costs:")
    for cost in COSTS:
        cr = cost / med_risk
        net = R.mean() - cr
        print(f"    {cost:.2f}% round trip -> -{cr:.3f} R  "
              f"=> {net:+.3f} R/trade  ({net*len(R):+.1f} R total)  "
              f"{'OK' if net > 0 else 'NEGATIVE'}")

    print(f"\n  Holding:")
    print(f"    median {np.median([t['bars'] for t in trades]):.0f} bars "
          f"({np.median([t['bars'] for t in trades])*30/60:.1f} h) · "
          f"max {max(t['bars'] for t in trades)} bars")
    print(f"    held overnight : "
          f"{np.mean([t['overnight'] for t in trades])*100:.0f}% of trades")
    print(f"    max calendar days held : "
          f"{max(t['days'] for t in trades)}")

    # month by month - is it steady or one lucky run?
    print(f"\n  Month by month:")
    dfm = pd.DataFrame([{'m': t['exit_t'].strftime('%Y-%m'), 'R': t['R']}
                        for t in trades])
    g = dfm.groupby('m')['R'].agg(['count', 'sum'])
    for m, r in g.iterrows():
        bar = '#' * int(abs(r['sum'])) if abs(r['sum']) < 40 else '#' * 40
        print(f"    {m}   {int(r['count']):>3} trades  {r['sum']:>+7.1f} R  {bar}")
    pos_months = (g['sum'] > 0).sum()
    print(f"    {pos_months}/{len(g)} months positive")

    print(f"\n  Longs  : {sum(t['R'] for t in trades if t['dir']=='BUY'):+.1f} R")
    print(f"  Shorts : {sum(t['R'] for t in trades if t['dir']=='SELL'):+.1f} R")


if __name__ == "__main__":
    main()
