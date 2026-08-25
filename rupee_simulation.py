"""
Rupee simulation on Rs 1,00,000 of capital
==========================================

Converts R-multiples into money, with an itemised Indian charges model, and
asks the question the R figures cannot: what is actually left at the end,
and how much went to the exchange, the broker and the taxman?

Charges modelled (NIFTY futures, discount broker)
-------------------------------------------------
  Brokerage   Rs 20 per executed order  -> Rs 40 per round trip (flat)
  STT         0.02%  on the SELL side only
  Exchange    0.00173% each side
  SEBI        0.0001% each side
  Stamp duty  0.002% on the BUY side only
  GST         18% on (brokerage + exchange + SEBI)
  Slippage    assumed separately - usually larger than all of the above

Flat brokerage matters disproportionately at small position sizes, so it is
modelled as a flat amount rather than folded into a percentage.

Position sizing
---------------
Risk a fixed fraction of CURRENT equity per trade, so gains compound and
losses shrink the next position - the way the account would actually behave.
Position notional follows from the stop distance: a 0.39% stop risking
Rs 1,000 implies about Rs 2.5 lakh of exposure, which is where the margin
question bites.
"""

import logging
import numpy as np
import pandas as pd

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI
from robustness_test import trade as trade_swing
from intraday_test import trade_intraday

CAPITAL = 100_000
OPT_START = pd.Timestamp('2026-02-24')

# charge rates
BROKERAGE_PER_ORDER = 20.0
STT_SELL = 0.0002        # 0.02%
EXCHANGE = 0.0000173     # 0.00173% each side
SEBI = 0.000001          # 0.0001% each side
STAMP_BUY = 0.00002      # 0.002%
GST = 0.18
SLIPPAGE_PCT = 0.0001    # 0.01% of notional, round trip

NIFTY_LOT = 75


def charges(notional):
    """Itemised round-trip cost on one position of `notional` rupees."""
    brokerage = BROKERAGE_PER_ORDER * 2
    stt = notional * STT_SELL
    exch = notional * EXCHANGE * 2
    sebi = notional * SEBI * 2
    stamp = notional * STAMP_BUY
    gst = (brokerage + exch + sebi) * GST
    slip = notional * SLIPPAGE_PCT
    return {'brokerage': brokerage, 'stt': stt, 'exchange': exch,
            'sebi': sebi, 'stamp': stamp, 'gst': gst, 'slippage': slip,
            'total': brokerage + stt + exch + sebi + stamp + gst + slip}


def simulate_money(trades, stop_pct, risk_frac, capital=CAPITAL):
    """Walk the trade list in rupees, compounding position size."""
    eq = capital
    peak = capital
    max_dd = 0.0
    totals = {k: 0.0 for k in
              ('brokerage', 'stt', 'exchange', 'sebi', 'stamp', 'gst',
               'slippage', 'total')}
    notionals = []
    curve = [eq]

    for t in trades:
        risk_amt = eq * risk_frac
        notional = risk_amt / (stop_pct / 100.0)
        notionals.append(notional)
        ch = charges(notional)
        for k in totals:
            totals[k] += ch[k]
        eq += t['R'] * risk_amt - ch['total']
        curve.append(eq)
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
        if eq <= 0:
            break

    return {'end': eq, 'curve': curve, 'charges': totals,
            'max_dd': max_dd, 'avg_notional': float(np.mean(notionals)),
            'max_notional': float(np.max(notionals)), 'n': len(trades)}


def report(name, trades, stop_pct, risk_fracs=(0.01, 0.02)):
    print(f"\n{'=' * 80}")
    print(f"  {name}")
    print(f"{'=' * 80}")
    R = np.array([t['R'] for t in trades])
    print(f"  {len(R)} trades · {(R>0).mean()*100:.1f}% win · "
          f"gross {R.sum():+.1f} R · 1R = {stop_pct:.3f}% of price")

    for rf in risk_fracs:
        r = simulate_money(trades, stop_pct, rf)
        ch = r['charges']
        gross = r['end'] - CAPITAL + ch['total']
        ret = (r['end'] / CAPITAL - 1) * 100

        print(f"\n  --- risking {rf*100:.0f}% of equity per trade ---")
        print(f"    Starting capital        Rs {CAPITAL:>12,.0f}")
        print(f"    Gross P&L               Rs {gross:>12,.0f}")
        print(f"    Total charges           Rs {-ch['total']:>12,.0f}")
        print(f"    {'-'*44}")
        print(f"    ENDING CAPITAL          Rs {r['end']:>12,.0f}"
              f"   ({ret:+.1f}%)")
        print(f"    Max drawdown            Rs {r['max_dd']:>12,.0f}")
        print(f"\n    Charges breakdown:")
        for k in ('brokerage', 'stt', 'exchange', 'sebi', 'stamp', 'gst',
                  'slippage'):
            share = ch[k] / ch['total'] * 100 if ch['total'] else 0
            print(f"      {k:<12} Rs {ch[k]:>10,.0f}   ({share:>4.1f}%)")
        print(f"      {'TOTAL':<12} Rs {ch['total']:>10,.0f}")
        print(f"      charges as % of gross P&L: "
              f"{ch['total']/gross*100 if gross else 0:.1f}%")
        print(f"      cost per trade           : Rs {ch['total']/r['n']:,.0f}")
        print(f"\n    Position size needed:")
        print(f"      avg notional            Rs {r['avg_notional']:>12,.0f}"
              f"   ({r['avg_notional']/CAPITAL:.1f}x capital)")
        print(f"      max notional            Rs {r['max_notional']:>12,.0f}")
    return trades


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

    print("=" * 80)
    print(f"  RUPEE SIMULATION - Rs {CAPITAL:,} capital")
    print(f"  NIFTY 50 index · 30-min · {idx[0].date()} -> {idx[-1].date()}"
          f"  (~{(idx[-1]-idx[0]).days/365:.2f} years)")
    print("=" * 80)

    configs = [
        ("SWING  key1.5 atr10 R:R2.0  (holds overnight)", 1.5, 10, 2.0, False),
        ("INTRADAY  key1.5 atr10 R:R3.0  (flat at close)", 1.5, 10, 3.0, True),
    ]

    for name, kv, ap, rr, intraday in configs:
        ut = TI.ut_bot_alert(h, lo, c, key_value=kv, atr_period=ap)
        b = ut['buy_signal'].astype(bool)
        s = ut['sell_signal'].astype(bool)
        t = ut['trailing_stop']

        risks = []
        for i in range(n - 1):
            if b[i] or s[i]:
                e = o[i + 1]
                rr_ = (e - t[i]) * (1 if b[i] else -1)
                if rr_ > 0 and e > 0:
                    risks.append(rr_ / e * 100)
        stop_pct = float(np.median(risks))

        trades = (trade_intraday(o, h, lo, c, b, s, t, lod, 0, n, rr)
                  if intraday else
                  trade_swing(o, h, lo, c, b, s, t, 0, n, rr))
        report(name, trades, stop_pct)

    # ---- can Rs 1 lakh actually trade this? --------------------------
    px = float(c[-1])
    lot_notional = NIFTY_LOT * px
    ut = TI.ut_bot_alert(h, lo, c, key_value=1.5, atr_period=10)
    b = ut['buy_signal'].astype(bool)
    s = ut['sell_signal'].astype(bool)
    t = ut['trailing_stop']
    risks = [((o[i+1] - t[i]) * (1 if b[i] else -1)) / o[i+1] * 100
             for i in range(n-1) if (b[i] or s[i]) and o[i+1] > 0
             and ((o[i+1] - t[i]) * (1 if b[i] else -1)) > 0]
    stop_pct = float(np.median(risks))
    risk_per_lot = lot_notional * stop_pct / 100

    print(f"\n{'=' * 80}")
    print("  REALITY CHECK - what can Rs 1,00,000 actually trade?")
    print(f"{'=' * 80}")
    print(f"    NIFTY spot (last bar)        {px:>12,.0f}")
    print(f"    Lot size                     {NIFTY_LOT:>12}")
    print(f"    Notional per lot          Rs {lot_notional:>12,.0f}"
          f"   ({lot_notional/CAPITAL:.1f}x your capital)")
    print(f"    Stop distance                {stop_pct:>11.3f}%")
    print(f"    RISK PER LOT              Rs {risk_per_lot:>12,.0f}"
          f"   = {risk_per_lot/CAPITAL*100:.1f}% of capital per trade")
    print(f"\n    A 1% risk budget wants     Rs {CAPITAL*0.01:>12,.0f} at risk")
    print(f"    One lot forces             Rs {risk_per_lot:>12,.0f} at risk")
    print(f"    -> smallest tradeable size is "
          f"{risk_per_lot/(CAPITAL*0.01):.1f}x too large for 1% risk")


if __name__ == "__main__":
    main()
