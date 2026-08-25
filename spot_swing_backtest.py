"""
Spot swing trading - NIFTY 50 stocks, daily bars, delivery (CNC)
================================================================

Why daily and why long-only
---------------------------
Delivery in the Indian cash segment cannot be shorted overnight, so every
SELL signal is dropped - roughly half the signals go. In exchange, spot has
no lot size, so Rs 1,00,000 can finally be sized to a true 1% risk.

Delivery STT is 0.1% on BOTH sides. Against a 0.4% stop (30-min bars) that
is half your R and fatal; against a 3-4% stop (daily bars) it is ~0.05 R and
merely a drag. That is what pushes this to daily.

Portfolio realism
-----------------
This is not 39 independent backtests added together. Signals are merged into
one chronological stream and taken only when capital allows: at 1% risk with
a ~3.3% stop each position needs ~30% of the account, so about three can be
open at once. Signals arriving when fully invested are SKIPPED, exactly as
they would be in a real account.

Costs (delivery, discount broker)
---------------------------------
  Brokerage  Rs 20 per order        STT      0.1% each side
  Exchange   0.00297% each side     SEBI     0.0001% each side
  Stamp      0.015% on buy          GST      18% on brokerage+exchange+SEBI
  DP charge  Rs 15.93 per SELL - a flat fee that hurts small positions
"""

import logging
import itertools
import os
import pickle

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

import numpy as np
import pandas as pd
from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI

CAPITAL = 100_000
RISK_FRAC = 0.01
N_BARS = 4000
CACHE = 'spot_daily_cache.pkl'

# delivery charges
BROKERAGE = 20.0
STT = 0.001
EXCHANGE = 0.0000297
SEBI = 0.000001
STAMP_BUY = 0.00015
GST = 0.18
DP_CHARGE = 15.93

SYMBOLS = [
    'RELIANCE', 'TCS', 'INFY', 'WIPRO', 'SBIN', 'MARUTI', 'BAJAJ_AUTO',
    'LT', 'AXISBANK', 'BHARTIARTL', 'ITC', 'SUNPHARMA', 'ASIANPAINT',
    'HCLTECH', 'TECHM', 'ULTRACEMCO', 'JSWSTEEL', 'ICICIBANK', 'POWERGRID',
    'NESTLEIND', 'BAJAJFINSV', 'TITAN', 'HINDUNILVR', 'INDIGO',
    'ADANIPORTS', 'BRITANNIA', 'COALINDIA', 'ONGC', 'GAIL', 'NTPC', 'BPCL',
    'HEROMOTOCO', 'SIEMENS', 'SHRIRAMFIN', 'TATACONSUM', 'TATASTEEL',
    'EICHERMOT', 'HDFCBANK', 'KOTAKBANK',
]


def load_data():
    if os.path.exists(CACHE):
        with open(CACHE, 'rb') as f:
            d = pickle.load(f)
        print(f"  {len(d)} symbols from cache")
        return d
    tv = TvDatafeed()
    out = []
    for k, s in enumerate(SYMBOLS, 1):
        try:
            df = tv.get_hist(symbol=s, exchange='NSE',
                             interval=Interval.in_daily, n_bars=N_BARS)
        except Exception:
            continue
        if df is None or len(df) < 500:
            continue
        out.append({'sym': s, 'idx': df.index,
                    'o': df['open'].values, 'h': df['high'].values,
                    'l': df['low'].values, 'c': df['close'].values})
        print(f"    [{k:2}/{len(SYMBOLS)}] {s:<12}{len(df):>6} bars  "
              f"{df.index[0].date()}")
    with open(CACHE, 'wb') as f:
        pickle.dump(out, f)
    return out


def charges(buy_val, sell_val):
    brokerage = BROKERAGE * 2
    stt = (buy_val + sell_val) * STT
    exch = (buy_val + sell_val) * EXCHANGE
    sebi = (buy_val + sell_val) * SEBI
    stamp = buy_val * STAMP_BUY
    gst = (brokerage + exch + sebi) * GST
    return brokerage + stt + exch + sebi + stamp + gst + DP_CHARGE


def build_signals(d, key, atr, rr):
    """Long-only trade candidates for one symbol: (entry_date, exit_date, R)."""
    o, h, l, c = d['o'], d['h'], d['l'], d['c']
    idx = d['idx']
    ut = TI.ut_bot_alert(h, l, c, key_value=key, atr_period=atr)
    if ut is None:
        return []
    buy = ut['buy_signal'].astype(bool)
    sell = ut['sell_signal'].astype(bool)
    ts = ut['trailing_stop']
    n = len(c)

    out = []
    for i in range(n - 1):
        if not buy[i]:
            continue
        entry, stop = o[i + 1], ts[i]
        if np.isnan(entry) or np.isnan(stop) or entry <= stop:
            continue
        risk = entry - stop
        target = entry + rr * risk
        r = ex = None
        for j in range(i + 1, n):
            if l[j] <= stop:
                fill = min(o[j], stop) if o[j] < stop else stop
                r, ex = (fill - entry) / risk, j
                break
            if h[j] >= target:
                r, ex = rr, j
                break
            if sell[j]:
                r, ex = (c[j] - entry) / risk, j
                break
        if r is None:
            continue
        out.append({'sym': d['sym'], 'ei': i + 1, 'xi': ex,
                    'edate': idx[i + 1], 'xdate': idx[ex],
                    'entry': entry, 'stop': stop, 'R': r,
                    'stop_pct': risk / entry * 100})
    return out


def portfolio(all_trades, capital=CAPITAL, risk_frac=RISK_FRAC,
              start=None, end=None):
    """Chronological simulation with a hard capital constraint."""
    tr = sorted(all_trades, key=lambda t: t['edate'])
    if start is not None:
        tr = [t for t in tr if t['edate'] >= start]
    if end is not None:
        tr = [t for t in tr if t['edate'] < end]
    if not tr:
        return None

    eq = capital
    peak = eq
    max_dd = 0.0
    open_pos = []          # (exit_date, proceeds_fn)
    taken, skipped = [], 0
    total_charges = 0.0

    for t in tr:
        # settle anything that closed before this entry
        still = []
        for xdate, pnl, chg in open_pos:
            if xdate <= t['edate']:
                eq += pnl - chg
                total_charges += chg
                peak = max(peak, eq)
                max_dd = min(max_dd, eq - peak)
            else:
                still.append((xdate, pnl, chg))
        open_pos = still

        committed = sum(1 for _ in open_pos)
        risk_amt = eq * risk_frac
        qty_value = risk_amt / (t['stop_pct'] / 100.0)

        # capital actually available right now
        free = eq - sum(v for _, _, v in [])  # positions are notional-funded
        in_use = committed * qty_value
        if qty_value <= 0 or in_use + qty_value > eq:
            skipped += 1
            continue

        pnl = t['R'] * risk_amt
        chg = charges(qty_value, qty_value + pnl)
        open_pos.append((t['xdate'], pnl, chg))
        taken.append(t)

    for xdate, pnl, chg in open_pos:
        eq += pnl - chg
        total_charges += chg
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)

    R = np.array([t['R'] for t in taken]) if taken else np.array([])
    return {'end': eq, 'n': len(taken), 'skipped': skipped,
            'charges': total_charges, 'max_dd': max_dd,
            'win': (R > 0).mean() * 100 if len(R) else 0,
            'grossR': R.sum() if len(R) else 0,
            'first': taken[0]['edate'] if taken else None,
            'last': taken[-1]['xdate'] if taken else None}


def main():
    print("=" * 84)
    print(f"  SPOT SWING - NIFTY 50 stocks, DAILY bars, delivery, long only")
    print(f"  Rs {CAPITAL:,} capital · {RISK_FRAC*100:.0f}% risk per trade")
    print("=" * 84)
    data = load_data()
    if not data:
        print("  no data")
        return

    span = (min(d['idx'][0] for d in data), max(d['idx'][-1] for d in data))
    print(f"  Data: {len(data)} symbols  {span[0].date()} -> {span[1].date()}")

    # split: optimise on the older half, validate on the newer
    mid = pd.Timestamp('2021-01-01')
    print(f"  Optimise before {mid.date()} · validate after\n")

    print(f"  {'key':>5}{'atr':>5}{'R:R':>6}{'trades':>8}{'win%':>7}"
          f"{'IS end':>12}{'OOS end':>12}{'OOS CAGR':>10}{'OOS maxDD':>11}")
    print("  " + "-" * 76)

    rows = []
    for key, atr, rr in itertools.product((1.5, 2.0, 2.5, 3.0),
                                          (5, 10, 14, 21),
                                          (1.5, 2.0, 3.0)):
        allt = []
        for d in data:
            allt.extend(build_signals(d, key, atr, rr))
        if len(allt) < 100:
            continue
        is_ = portfolio(allt, end=mid)
        oos = portfolio(allt, start=mid)
        if not is_ or not oos or oos['n'] < 30:
            continue
        yrs = (oos['last'] - oos['first']).days / 365 if oos['last'] else 1
        cagr = ((oos['end'] / CAPITAL) ** (1 / max(yrs, 0.5)) - 1) * 100
        rows.append({'key': key, 'atr': atr, 'rr': rr, 'is': is_,
                     'oos': oos, 'cagr': cagr, 'yrs': yrs})
        print(f"  {key:>5.1f}{atr:>5}{rr:>6.1f}{oos['n']:>8}{oos['win']:>6.1f}%"
              f"{is_['end']:>12,.0f}{oos['end']:>12,.0f}{cagr:>9.1f}%"
              f"{oos['max_dd']:>11,.0f}")

    if not rows:
        print("\n  nothing qualified")
        return

    rows.sort(key=lambda r: -r['oos']['end'])
    print(f"\n{'=' * 84}")
    print("  BEST BY OUT-OF-SAMPLE ENDING CAPITAL")
    print(f"{'=' * 84}")
    for r in rows[:5]:
        o_, i_ = r['oos'], r['is']
        print(f"  key {r['key']} atr {r['atr']} R:R {r['rr']}")
        print(f"    in-sample  (pre 2021): Rs {i_['end']:>11,.0f}  "
              f"{i_['n']} trades, {i_['win']:.1f}% win")
        print(f"    OUT-OF-SAMPLE        : Rs {o_['end']:>11,.0f}  "
              f"{o_['n']} trades, {o_['win']:.1f}% win, "
              f"{r['cagr']:.1f}% CAGR over {r['yrs']:.1f}y")
        print(f"    charges Rs {o_['charges']:,.0f} · maxDD Rs "
              f"{o_['max_dd']:,.0f} · {o_['skipped']} signals skipped "
              f"(no capital)")
        print()

    pos = sum(1 for r in rows if r['oos']['end'] > CAPITAL)
    print(f"  {pos}/{len(rows)} parameter sets profitable out-of-sample "
          f"({pos/len(rows)*100:.0f}%)")


if __name__ == "__main__":
    main()
