"""Verify tvDatafeed timestamp alignment + completeness + signal parity"""
import logging
logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

from tvDatafeed import TvDatafeed, Interval
import pandas as pd, numpy as np, pytz
from datetime import datetime
from tradingview_indicators import TradingViewIndicators

pd.set_option('display.width', 200)

tv = TvDatafeed()
df = tv.get_hist(symbol="RELIANCE", exchange="NSE",
                 interval=Interval.in_30_minute, n_bars=500)

print("="*78)
print("1. TIMESTAMP ALIGNMENT")
print("="*78)
print(f"  Raw index tz      : {df.index.tz}")
print(f"  First bar (raw)   : {df.index[0]}")
print(f"  Last  bar (raw)   : {df.index[-1]}")

# Hypothesis: naive timestamps are in LOCAL machine tz (Europe/Berlin = UTC+2 in Aug)
local = pytz.timezone('Europe/Berlin')
ist   = pytz.timezone('Asia/Kolkata')
idx_ist = df.index.tz_localize(local).tz_convert(ist)
print(f"\n  Reinterpreted as Europe/Berlin -> IST:")
print(f"  First bar (IST)   : {idx_ist[0]}")
print(f"  Last  bar (IST)   : {idx_ist[-1]}")

# Indian session = 09:15 - 15:30 IST. Valid 30m bar starts: 09:15..15:00
times = sorted(set(idx_ist.strftime('%H:%M')))
print(f"\n  Distinct bar times (IST): {times}")
expected = ['09:15','09:45','10:15','10:45','11:15','11:45','12:15','12:45',
            '13:15','13:45','14:15','14:45','15:15']
print(f"  Expected NSE 30m starts : {expected}")
print(f"  MATCH: {set(times) == set(expected)}")

print("\n" + "="*78)
print("2. COMPLETENESS - bars per trading day")
print("="*78)
tmp = df.copy(); tmp.index = idx_ist
per_day = tmp.groupby(tmp.index.date).size()
print(per_day.tail(10).to_string())
print(f"\n  A full NSE day = 13 bars (09:15 -> 15:15)")

print("\n" + "="*78)
print("3. LAST SESSION - full detail")
print("="*78)
last_day = per_day.index[-1]
print(tmp[tmp.index.date == last_day][['open','high','low','close']].to_string())

print("\n" + "="*78)
print("4. SIGNAL TEST - run YOUR Pine Script logic on TradingView data")
print("="*78)
ut = TradingViewIndicators.ut_bot_alert(
        df['high'].values, df['low'].values, df['close'].values,
        key_value=2, atr_period=1)
lr = TradingViewIndicators.linear_reg_candles(
        df['open'].values, df['high'].values, df['low'].values, df['close'].values,
        signal_length=6, use_sma=True, linreg_length=8)

buys  = np.where(ut['buy_signal'])[0]
sells = np.where(ut['sell_signal'])[0]
print(f"  Bars analysed : {len(df)}")
print(f"  BUY signals   : {len(buys)}")
print(f"  SELL signals  : {len(sells)}")
print(f"\n  Last 8 signals (IST) - VERIFY THESE ON YOUR TRADINGVIEW CHART:")
evts = sorted([(i,'BUY') for i in buys] + [(i,'SELL') for i in sells])[-8:]
for i, kind in evts:
    print(f"    {idx_ist[i].strftime('%Y-%m-%d %H:%M')} IST  {kind:4s}  @ Rs.{df['close'].values[i]:.2f}"
          f"   LinReg={'GREEN' if lr['green_candles'][i] else 'RED'}")
