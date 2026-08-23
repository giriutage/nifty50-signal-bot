"""
TradingView Indicators in Python
Exact implementation of UT Bot Alert and Linear Regression Candles
"""

import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)


class TradingViewIndicators:
    """Exact TradingView indicator implementations"""

    @staticmethod
    def atr(high, low, close, period):
        """Calculate Average True Range (using EMA like Pine Script)"""
        high = np.array(high, dtype=float)
        low = np.array(low, dtype=float)
        close = np.array(close, dtype=float)

        # Calculate True Range
        tr = []
        for i in range(len(close)):
            if i == 0:
                tr.append(high[i] - low[i])
            else:
                tr.append(max(
                    high[i] - low[i],
                    abs(high[i] - close[i-1]),
                    abs(low[i] - close[i-1])
                ))

        # Calculate EMA of TR (Pine Script uses EMA for ATR)
        tr = np.array(tr)
        atr_values = TradingViewIndicators.ema(tr, period)
        return atr_values

    @staticmethod
    def linreg(values, length):
        """Calculate Linear Regression values"""
        values = np.array(values, dtype=float)
        result = []

        for i in range(len(values)):
            if i < length - 1:
                result.append(np.nan)
            else:
                # Get the last 'length' values
                y = values[i - length + 1:i + 1]

                # Use centered x-axis (matches Pine Script's linreg)
                x = np.arange(length) - (length - 1) / 2.0

                # Linear regression with centered x
                x_mean = np.mean(x)
                y_mean = np.mean(y)

                numerator = np.sum((x - x_mean) * (y - y_mean))
                denominator = np.sum((x - x_mean) ** 2)

                if denominator == 0:
                    result.append(y_mean)
                else:
                    slope = numerator / denominator
                    intercept = y_mean - slope * x_mean
                    # Value at the current bar (rightmost position)
                    # With centered x, current position is at (length-1)/2
                    reg_value = slope * ((length - 1) / 2.0) + intercept
                    result.append(reg_value)

        return np.array(result)

    @staticmethod
    def sma(values, length):
        """Calculate Simple Moving Average"""
        values = np.array(values, dtype=float)
        result = []

        for i in range(len(values)):
            if i < length - 1:
                result.append(np.nan)
            else:
                result.append(np.mean(values[i - length + 1:i + 1]))

        return np.array(result)

    @staticmethod
    def ema(values, length):
        """Calculate Exponential Moving Average"""
        values = np.array(values, dtype=float)
        result = []
        multiplier = 2.0 / (length + 1)

        for i in range(len(values)):
            if i < length - 1:
                result.append(np.nan)
            elif i == length - 1:
                result.append(np.mean(values[:i + 1]))
            else:
                ema_val = values[i] * multiplier + result[i - 1] * (1 - multiplier)
                result.append(ema_val)

        return np.array(result)

    @staticmethod
    def ut_bot_alert(high, low, close, key_value=2, atr_period=1):
        """
        UT Bot Alert Indicator

        Args:
            high, low, close: OHLC arrays
            key_value: "Key Value" parameter (default 2)
            atr_period: ATR period (default 1)

        Returns:
            dict with 'buy' and 'sell' signals
        """
        try:
            high = np.array(high, dtype=float)
            low = np.array(low, dtype=float)
            close = np.array(close, dtype=float)

            if len(close) < max(atr_period + 1, 2):
                return None

            # Calculate ATR
            xatr = TradingViewIndicators.atr(high, low, close, atr_period)
            nloss = key_value * xatr

            # Initialize trailing stop
            xatr_trailing_stop = np.zeros(len(close))

            # Calculate trailing stop
            for i in range(len(close)):
                if i == 0:
                    xatr_trailing_stop[i] = close[i]
                else:
                    prev_stop = xatr_trailing_stop[i - 1]
                    src = close[i]
                    src_prev = close[i - 1]
                    loss = nloss[i]

                    if np.isnan(loss):
                        xatr_trailing_stop[i] = src
                    elif src > prev_stop and src_prev > prev_stop:
                        xatr_trailing_stop[i] = max(prev_stop, src - loss)
                    elif src < prev_stop and src_prev < prev_stop:
                        xatr_trailing_stop[i] = min(prev_stop, src + loss)
                    elif src > prev_stop:
                        xatr_trailing_stop[i] = src - loss
                    else:
                        xatr_trailing_stop[i] = src + loss

            # Calculate position
            pos = np.zeros(len(close))
            for i in range(len(close)):
                if i == 0:
                    pos[i] = 0
                else:
                    src = close[i]
                    src_prev = close[i - 1]
                    stop = xatr_trailing_stop[i - 1]

                    if src_prev < stop and src > stop:
                        pos[i] = 1  # BUY
                    elif src_prev > stop and src < stop:
                        pos[i] = -1  # SELL
                    else:
                        pos[i] = pos[i - 1]

            # Calculate EMA for confirmation
            ema_vals = TradingViewIndicators.ema(close, 1)

            # Check for CROSSOVER events (Pine Script requirement)
            # Buy crossover: EMA crosses above trailing stop
            # Sell crossover: trailing stop crosses above EMA (equivalently, EMA crosses below trailing stop)

            buy_crossover = []
            sell_crossover = []
            buy_signal_final = []
            sell_signal_final = []

            for i in range(len(close)):
                if i == 0:
                    buy_crossover.append(False)
                    sell_crossover.append(False)
                    buy_signal_final.append(False)
                    sell_signal_final.append(False)
                else:
                    src = close[i]
                    src_prev = close[i - 1]
                    stop = xatr_trailing_stop[i]
                    stop_prev = xatr_trailing_stop[i - 1]
                    ema_curr = ema_vals[i]
                    ema_prev = ema_vals[i - 1]

                    # Crossover: EMA crosses ABOVE trailing stop
                    ema_crosses_above = (ema_prev <= stop_prev) and (ema_curr > stop)

                    # Crossover: EMA crosses BELOW trailing stop (or trailing stop crosses above EMA)
                    ema_crosses_below = (ema_prev >= stop_prev) and (ema_curr < stop)

                    # Buy: src > trailing_stop AND ema crosses above trailing stop
                    buy = (src > stop) and ema_crosses_above

                    # Sell: src < trailing_stop AND ema crosses below trailing stop
                    sell = (src < stop) and ema_crosses_below

                    buy_crossover.append(ema_crosses_above)
                    sell_crossover.append(ema_crosses_below)
                    buy_signal_final.append(buy)
                    sell_signal_final.append(sell)

            return {
                'trailing_stop': xatr_trailing_stop,
                'position': pos,
                'buy_signal': np.array(buy_signal_final),
                'sell_signal': np.array(sell_signal_final),
                'buy_crossover': np.array(buy_crossover),
                'sell_crossover': np.array(sell_crossover),
                'ema': ema_vals
            }

        except Exception as e:
            logger.error(f"Error in UT Bot Alert: {str(e)}")
            return None

    @staticmethod
    def linear_reg_candles(open_prices, high, low, close, signal_length=6, use_sma=True, linreg_length=8):
        """
        Linear Regression Candles Indicator

        Args:
            open_prices, high, low, close: OHLC arrays
            signal_length: Smoothing period (default 6)
            use_sma: Use SMA for signal (default True, else EMA)
            linreg_length: Linear regression length (default 8)

        Returns:
            dict with LinReg OHLC values and signal
        """
        try:
            open_prices = np.array(open_prices, dtype=float)
            high = np.array(high, dtype=float)
            low = np.array(low, dtype=float)
            close = np.array(close, dtype=float)

            if len(close) < linreg_length:
                return None

            # Calculate linear regression for OHLC
            linreg_open = TradingViewIndicators.linreg(open_prices, linreg_length)
            linreg_high = TradingViewIndicators.linreg(high, linreg_length)
            linreg_low = TradingViewIndicators.linreg(low, linreg_length)
            linreg_close = TradingViewIndicators.linreg(close, linreg_length)

            # Calculate signal line
            if use_sma:
                signal = TradingViewIndicators.sma(linreg_close, signal_length)
            else:
                signal = TradingViewIndicators.ema(linreg_close, signal_length)

            # Determine candle color (green if close > open, red if close < open)
            green_candles = linreg_close > linreg_open
            red_candles = linreg_close < linreg_open

            return {
                'open': linreg_open,
                'high': linreg_high,
                'low': linreg_low,
                'close': linreg_close,
                'signal': signal,
                'green_candles': green_candles,
                'red_candles': red_candles
            }

        except Exception as e:
            logger.error(f"Error in Linear Regression Candles: {str(e)}")
            return None

    @staticmethod
    def generate_signals(open_prices, high, low, close, symbol=""):
        """
        Generate combined BUY/SELL signals from both indicators

        Settings:
        - UT Bot: Key Value = 2, ATR Period = 1
        - LinReg: Signal Smoothing = 6, SMA = True, Length = 8
        """
        try:
            # UT Bot Alert signals
            ut_signals = TradingViewIndicators.ut_bot_alert(
                high, low, close,
                key_value=2,
                atr_period=1
            )

            # Linear Regression Candles signals
            lr_signals = TradingViewIndicators.linear_reg_candles(
                open_prices, high, low, close,
                signal_length=6,
                use_sma=True,
                linreg_length=8
            )

            if ut_signals is None or lr_signals is None:
                return None

            # Get latest values
            latest_idx = len(close) - 1

            ut_buy = ut_signals['buy_signal'][latest_idx]
            ut_sell = ut_signals['sell_signal'][latest_idx]
            ut_trailing_stop = ut_signals['trailing_stop'][latest_idx]

            lr_close = lr_signals['close'][latest_idx]
            lr_open = lr_signals['open'][latest_idx]
            lr_green = lr_signals['green_candles'][latest_idx]
            lr_red = lr_signals['red_candles'][latest_idx]

            current_price = float(close[latest_idx])

            # IMPORTANT: Only UT Bot generates signals
            # LinReg Candles is just visual confirmation (shows red/green candles)

            signal = None
            confidence = "low"
            details = ""

            # Only trigger signal if UT Bot has a signal
            if ut_buy:
                signal = "BUY"
                if lr_green:
                    confidence = "high"
                    details = "UT Bot BUY + LinReg GREEN confirmation"
                else:
                    confidence = "medium"
                    details = "UT Bot BUY (LinReg is RED - caution)"

            elif ut_sell:
                signal = "SELL"
                if lr_red:
                    confidence = "high"
                    details = "UT Bot SELL + LinReg RED confirmation"
                else:
                    confidence = "medium"
                    details = "UT Bot SELL (LinReg is GREEN - caution)"

            if signal:
                return {
                    'symbol': symbol,
                    'signal': signal,
                    'price': current_price,
                    'confidence': confidence,
                    'details': details,
                    'ut_trailing_stop': float(ut_trailing_stop),
                    'lr_open': float(lr_open),
                    'lr_close': float(lr_close),
                }

            # No UT Bot signal = No signal at all
            return None

        except Exception as e:
            logger.error(f"Error generating signals: {str(e)}")
            return None


if __name__ == "__main__":
    import yfinance as yf

    # Test with INFY
    print("Fetching INFY data...")
    data = yf.download('INFY.NS', period='7d', interval='30m', progress=False)

    print(f"Data shape: {data.shape}")
    print(f"Latest close: {data['Close'].iloc[-1]}")

    signal_result = TradingViewIndicators.generate_signals(
        data['Open'].values.flatten(),
        data['High'].values.flatten(),
        data['Low'].values.flatten(),
        data['Close'].values.flatten(),
        symbol="INFY"
    )

    if signal_result:
        print(f"\nSignal Found!")
        print(f"Signal: {signal_result['signal']}")
        print(f"Price: {signal_result['price']:.2f}")
        print(f"Confidence: {signal_result['confidence']}")
        print(f"Details: {signal_result['details']}")
    else:
        print("\nNo signal at current bar")
