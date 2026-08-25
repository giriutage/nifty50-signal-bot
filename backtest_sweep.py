"""
Sensitivity sweep for the NIFTY50 5-min strategy.

Answers: is there a version of these rules that IS profitable? Varies the
reward-to-risk target, the exit rule, and the signal-line filter over the
same two months of data.

Data and indicator values are computed once and cached, so the sweep
itself is fast.
"""

import logging, os, pickle, sys
from datetime import datetime, timedelta

logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

import numpy as np
from tvDatafeed import TvDatafeed, Interval
from tradingview_indicators import TradingViewIndicators as TI
from backtest_nifty import (SYMBOLS, EXCHANGE, INTERVAL, N_BARS,
                            LOOKBACK_DAYS, KEY_VALUE, ATR_PERIOD,
                            SIGNAL_LENGTH, USE_SMA, LINREG_LENGTH)

CACHE = 'backtest_cache.pkl'


def load():
    if os.path.exists(CACHE):
        with open(CACHE, 'rb') as f:
            data = pickle.load(f)
        print(f"  Loaded {len(data)} symbols from cache.")
        return data

    print("  Fetching + computing indicators (once)...")
    tv = TvDatafeed()
    cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    data = []
    for k, sym in enumerate(SYMBOLS, 1):
        try:
            df = tv.get_hist(symbol=sym, exchange=EXCHANGE,
                             interval=INTERVAL, n_bars=N_BARS)
        except Exception:
            continue
        if df is None or len(df) < 200:
            continue
        df = df[df.index >= cutoff]
        if len(df) < 200:
            continue
        ut = TI.ut_bot_alert(df['high'].values, df['low'].values,
                             df['close'].values,
                             key_value=KEY_VALUE, atr_period=ATR_PERIOD)
        lr = TI.linear_reg_candles(df['open'].values, df['high'].values,
                                   df['low'].values, df['close'].values,
                                   signal_length=SIGNAL_LENGTH,
                                   use_sma=USE_SMA,
                                   linreg_length=LINREG_LENGTH)
        if ut is None or lr is None:
            continue
        data.append({
            'sym': sym,
            'o': df['open'].values, 'h': df['high'].values,
            'l': df['low'].values, 'c': df['close'].values,
            'buy': ut['buy_signal'], 'sell': ut['sell_signal'],
            'stop': ut['trailing_stop'],
            'line': lr['signal'], 'lrc': lr['close'],
        })
        print(f"    [{k:2}/{len(SYMBOLS)}] {sym}")
    with open(CACHE, 'wb') as f:
        pickle.dump(data, f)
    print(f"  Cached {len(data)} symbols.")
    return data


def run(d, rr, filter_mode, use_opposite_exit):
    o, h, lo, c = d['o'], d['h'], d['l'], d['c']
    buy, sell, tstop = d['buy'], d['sell'], d['stop']
    line, lrc = d['line'], d['lrc']
    n = len(c)

    out, ties = [], 0
    i = 0
    while i < n - 1:
        is_buy, is_sell = bool(buy[i]), bool(sell[i])
        if not (is_buy or is_sell):
            i += 1
            continue

        if filter_mode != 'none':
            ref = lrc[i] if filter_mode == 'linreg' else c[i]
            if np.isnan(line[i]) or np.isnan(ref) \
               or (is_buy and not ref > line[i]) \
               or (is_sell and not ref < line[i]):
                i += 1
                continue

        entry, stop = o[i + 1], tstop[i]
        if np.isnan(entry) or np.isnan(stop):
            i += 1
            continue
        direction = 1 if is_buy else -1
        risk = (entry - stop) * direction
        if risk <= 0:
            i += 1
            continue
        target = entry + direction * rr * risk

        outcome = exit_i = None
        for j in range(i + 1, n):
            if is_buy:
                hs, ht = lo[j] <= stop, h[j] >= target
            else:
                hs, ht = h[j] >= stop, lo[j] <= target
            if hs and ht:
                ties += 1
            if hs:
                outcome, exit_i = -1.0, j
                break
            if ht:
                outcome, exit_i = rr, j
                break
            if use_opposite_exit and ((is_buy and sell[j]) or (is_sell and buy[j])):
                outcome, exit_i = direction * (c[j] - entry) / risk, j
                break
        if outcome is None:
            break
        out.append(outcome)
        i = exit_i + 1
    return out, ties


def main():
    print("=" * 78)
    print("  SENSITIVITY SWEEP - NIFTY50 5-min, last ~2 months")
    print("=" * 78)
    data = load()

    print(f"\n{'=' * 78}")
    print(f"  {'filter':<12}{'exit rule':<18}{'R:R':>5}"
          f"{'trades':>9}{'win%':>8}{'break-even':>12}{'total R':>10}{'exp R':>9}")
    print(f"{'-' * 78}")

    best = []
    for filt, flabel in [('none', 'none'), ('linreg', 'linreg'), ('price', 'price')]:
        for opp, olabel in [(True, 'stop/target/flip'), (False, 'stop/target only')]:
            for rr in (1.0, 1.5, 2.0, 2.5, 3.0):
                allR, ties = [], 0
                for d in data:
                    r, t = run(d, rr, filt, opp)
                    allR.extend(r)
                    ties += t
                if not allR:
                    continue
                R = np.array(allR)
                wr = (R > 0).mean() * 100
                be = 100 / (1 + rr)
                tot, exp = R.sum(), R.mean()
                flag = ' <<<' if tot > 0 else ''
                print(f"  {flabel:<12}{olabel:<18}{rr:>5.1f}"
                      f"{len(R):>9}{wr:>7.1f}%{be:>11.1f}%"
                      f"{tot:>+10.1f}{exp:>+9.3f}{flag}")
                best.append((tot, flabel, olabel, rr, len(R), wr, exp))
            print()

    best.sort(reverse=True)
    print(f"{'=' * 78}")
    print("  BEST FIVE BY TOTAL R")
    print(f"{'=' * 78}")
    for tot, f, o, rr, n, wr, exp in best[:5]:
        print(f"  {tot:>+8.1f} R   filter={f:<8} exit={o:<18} "
              f"R:R={rr}   {n} trades, {wr:.1f}% win, {exp:+.3f} exp")

    if best[0][0] <= 0:
        print("\n  Nothing tested is profitable. The edge is not in these knobs.")


if __name__ == "__main__":
    main()
