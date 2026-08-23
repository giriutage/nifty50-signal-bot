"""
Measure the REAL lag between a bar closing in wall-clock time and that
closed bar becoming available from TradingView's feed.

Method: poll 1-minute bars on a 24/7 market. When a bar labelled T first
appears, the previous bar (T minus one minute) has just closed. The delay
between wall-clock T and that first sighting IS the feed lag.
"""
import logging
logging.getLogger('tvDatafeed').setLevel(logging.CRITICAL)

from tvDatafeed import TvDatafeed, Interval
from datetime import datetime
import time, statistics

POLL_SECONDS = 4
RUN_MINUTES = 6

LOCAL_TZ = datetime.now().astimezone().tzinfo
tv = TvDatafeed()

print("=" * 78)
print("FEED LAG MEASUREMENT  (BINANCE:BTCUSDT, 1-minute bars)")
print("=" * 78)
print(f"  Polling every {POLL_SECONDS}s for {RUN_MINUTES} minutes.")
print(f"  Measuring: wall-clock delay from bar boundary -> bar available.\n")

seen = None
samples = []
deadline = time.time() + RUN_MINUTES * 60
polls = errors = 0

while time.time() < deadline:
    try:
        df = tv.get_hist(symbol="BTCUSDT", exchange="BINANCE",
                         interval=Interval.in_1_minute, n_bars=3)
        observed_at = datetime.now(LOCAL_TZ)
        polls += 1

        if df is None or len(df) == 0:
            time.sleep(POLL_SECONDS); continue

        newest = df.index[-1]
        newest_aware = newest.tz_localize(LOCAL_TZ)

        if seen is None:
            seen = newest_aware
            print(f"  baseline: newest bar {newest.strftime('%H:%M:%S')}")
        elif newest_aware > seen:
            # Bar `newest` just began => bar (newest - 1min) just closed.
            lag = (observed_at - newest_aware).total_seconds()
            samples.append(lag)
            print(f"  [{len(samples)}] bar {newest.strftime('%H:%M:%S')} appeared at "
                  f"{observed_at.strftime('%H:%M:%S')}  ->  lag = {lag:5.1f}s")
            seen = newest_aware

    except Exception as e:
        errors += 1
        if errors <= 3:
            print(f"  (poll error: {type(e).__name__})")

    time.sleep(POLL_SECONDS)

print("\n" + "=" * 78)
print("RESULT")
print("=" * 78)
print(f"  Polls: {polls}   Errors: {errors}   Boundaries captured: {len(samples)}")

if samples:
    mx = max(samples)
    print(f"\n  min    : {min(samples):5.1f}s")
    print(f"  median : {statistics.median(samples):5.1f}s")
    print(f"  max    : {mx:5.1f}s")
    print(f"\n  Polling granularity is {POLL_SECONDS}s, so true lag is up to "
          f"{POLL_SECONDS}s lower than shown.")
    rec = max(60, int((mx + 30) / 60.0 + 0.999) * 60)
    print(f"\n  => A {int(rec/60)}-minute buffer after bar close is sufficient "
          f"for the FEED.")
    print(f"     (Scheduler jitter is a separate, larger factor - see notes.)")
else:
    print("\n  No boundary captured. Re-run for longer.")
