"""
Options backtest - swing signals expressed as NIFTY option buys
===============================================================

The underlying signals are the validated ones (Key Value 1.5, ATR Period 10,
30-min NIFTY). This asks what happens when they are traded as OPTIONS rather
than futures, since futures need ~Rs 7.2L to risk 1% per trade.

Option pricing
--------------
Black-Scholes, marked at entry and exit. Nothing here is real option data -
free historical NIFTY option chains do not exist - so implied volatility is
an assumption and is sensitivity-tested. What the model does capture, and
what matters most, is THETA: a long option bleeds every day it is held, and
the swing signals hold for days.

Charges (per the user's broker)
-------------------------------
  Brokerage   Rs 50 per leg  -> Rs 100 per round trip
  STT         0.1% of premium on the SELL side
  Exchange    0.03503% of premium, each side
  Stamp       0.003% of premium on the BUY side
  GST         18% on (brokerage + exchange)
Option charges are levied on PREMIUM turnover, not notional, which is why
they look small in rupees while the flat brokerage dominates.
"""

import logging
from datetime import datetime, timedelta

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

import numpy as np
import pandas as pd
from scipy.stats import norm
from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI
from robustness_test import trade as trade_swing
from intraday_test import trade_intraday

CAPITAL = 100_000
LOT = 75
STRIKE_STEP = 50
IV = 0.13                 # assumed; swept below
RATE = 0.065
BROKERAGE_LEG = 50.0
STT_SELL = 0.001          # 0.1% of premium
EXCHANGE = 0.0003503
STAMP_BUY = 0.00003
GST = 0.18


def bs_price(S, K, T, r, sigma, call=True):
    """Black-Scholes premium per unit. T in years."""
    if T <= 1e-9:
        return max(0.0, (S - K) if call else (K - S))
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if call:
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def next_thursday(d):
    """Weekly expiry - the coming Thursday (same day if it is Thursday)."""
    return d + timedelta(days=(3 - d.weekday()) % 7)


def option_charges(entry_prem, exit_prem):
    """Round-trip cost in rupees on one lot."""
    ev, xv = entry_prem * LOT, exit_prem * LOT
    brokerage = BROKERAGE_LEG * 2
    stt = xv * STT_SELL
    exch = (ev + xv) * EXCHANGE
    stamp = ev * STAMP_BUY
    gst = (brokerage + exch) * GST
    return brokerage + stt + exch + stamp + gst


def run(trades, idx, spot, iv=IV, label=""):
    """Express each underlying trade as a long ATM option on the same side."""
    rows = []
    for t in trades:
        i, j = t['i'], t['j']
        S0, S1 = spot[i], spot[j]
        t0, t1 = idx[i], idx[j]
        call = t.get('dir', 'BUY') == 'BUY'

        K = round(S0 / STRIKE_STEP) * STRIKE_STEP
        exp = next_thursday(t0.date())
        T0 = max((exp - t0.date()).days, 0) / 365.0
        T1 = max((exp - t1.date()).days, 0) / 365.0
        if T0 <= 0:                      # entered on expiry day
            continue

        p0 = bs_price(S0, K, T0, RATE, iv, call)
        p1 = bs_price(S1, K, T1, RATE, iv, call)
        if p0 <= 1.0:
            continue

        cost = option_charges(p0, p1)
        gross = (p1 - p0) * LOT
        rows.append({
            'R_underlying': t['R'], 'prem_in': p0, 'prem_out': p1,
            'gross': gross, 'cost': cost, 'net': gross - cost,
            'prem_pct': (p1 / p0 - 1) * 100,
            'lot_cost': p0 * LOT,
            'days': (t1.date() - t0.date()).days,
            'dir': 'CE' if call else 'PE',
        })
    return rows


def report(label, rows):
    if not rows:
        print(f"  {label}: no trades")
        return None
    net = np.array([r['net'] for r in rows])
    gross = np.array([r['gross'] for r in rows])
    cost = np.array([r['cost'] for r in rows])
    prem = np.array([r['lot_cost'] for r in rows])
    eq = np.cumsum(net)
    dd = (eq - np.maximum.accumulate(eq)).min()

    print(f"\n  {label}")
    print(f"    trades              {len(rows)}")
    print(f"    win rate            {(net>0).mean()*100:.1f}%")
    print(f"    premium per lot     Rs {prem.mean():,.0f} avg  "
          f"(= {prem.mean()/CAPITAL*100:.1f}% of Rs 1L, per trade)")
    print(f"    gross P&L           Rs {gross.sum():+,.0f}")
    print(f"    charges             Rs {-cost.sum():+,.0f}   "
          f"(Rs {cost.mean():,.0f}/trade)")
    print(f"    NET P&L             Rs {net.sum():+,.0f}")
    print(f"    per trade           Rs {net.mean():+,.0f}")
    print(f"    max drawdown        Rs {dd:+,.0f}")
    print(f"    avg hold            {np.mean([r['days'] for r in rows]):.1f} days")
    print(f"    avg premium change  {np.mean([r['prem_pct'] for r in rows]):+.1f}%")
    return {'net': net.sum(), 'dd': dd, 'n': len(rows)}


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

    ut = TI.ut_bot_alert(h, lo, c, key_value=1.5, atr_period=10)
    b = ut['buy_signal'].astype(bool)
    s = ut['sell_signal'].astype(bool)
    t = ut['trailing_stop']

    print("=" * 78)
    print(f"  OPTIONS BACKTEST - NIFTY, Rs {CAPITAL:,} capital")
    print(f"  {idx[0].date()} -> {idx[-1].date()}   lot {LOT}   "
          f"brokerage Rs {BROKERAGE_LEG:.0f}/leg")
    print(f"  Signals: UT Bot key 1.5 / atr 10 on 30-min (the validated set)")
    print("=" * 78)

    sw = trade_swing(o, h, lo, c, b, s, t, 0, n, 2.0)
    it = trade_intraday(o, h, lo, c, b, s, t, lod, 0, n, 3.0)
    # intraday helper does not carry direction; rebuild it
    for tr, src in ((sw, None),):
        pass

    print(f"\n  ---- SWING (holds overnight, {len(sw)} signals) ----")
    for iv in (0.11, 0.13, 0.16):
        rows = run(sw, idx, c, iv=iv)
        report(f"IV = {iv*100:.0f}%", rows)

    print(f"\n  ---- INTRADAY (flat at close, {len(it)} signals) ----")
    for iv in (0.11, 0.13, 0.16):
        rows = run(it, idx, c, iv=iv)
        report(f"IV = {iv*100:.0f}%", rows)

    # what does 1% risk even mean here?
    rows = run(sw, idx, c)
    if rows:
        prem = np.mean([r['lot_cost'] for r in rows])
        print(f"\n{'=' * 78}")
        print("  RISK REALITY AT Rs 1,00,000")
        print(f"{'=' * 78}")
        print(f"    Avg ATM premium per lot     Rs {prem:,.0f}")
        print(f"    That is                     {prem/CAPITAL*100:.1f}% of capital "
              f"committed per trade")
        print(f"    A 1% risk budget is         Rs {CAPITAL*0.01:,.0f}")
        print(f"    -> stop must be             {CAPITAL*0.01/prem*100:.1f}% "
              f"of premium")
        print(f"    Options routinely move that much in minutes, so such a stop "
              f"would be hit by noise alone.")
        print(f"\n    To risk 1% by losing the WHOLE premium instead, the option "
              f"must cost")
        print(f"    Rs {CAPITAL*0.01:,.0f} per lot = Rs "
              f"{CAPITAL*0.01/LOT:.1f} per unit - a deep out-of-the-money "
              f"strike with a low probability of paying out.")


if __name__ == "__main__":
    main()
