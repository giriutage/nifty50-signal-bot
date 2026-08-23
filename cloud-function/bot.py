"""
NIFTY50 Signal Bot - Local PC Version
Runs every 30 minutes via Windows Task Scheduler
Sends signals to Telegram
"""

import os
from dotenv import load_dotenv
import pandas as pd
import requests
from datetime import datetime, timedelta
from pyzdata import PyZData, Interval
from tradingview_indicators import TradingViewIndicators

# Load credentials
load_dotenv('.env_zerodha')
load_dotenv('.env')

ZERODHA_ENCTOKEN = os.getenv('ZERODHA_ENCTOKEN')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# NIFTY 50 stocks
NIFTY50_SYMBOLS = [
    'RELIANCE', 'TCS', 'INFY', 'HDFC', 'WIPRO', 'SBIN', 'MARUTI', 'BAJAJ-AUTO',
    'LT', 'AXISBANK', 'BHARTIARTL', 'ITC', 'SUNPHARMA', 'ASIANPAINT',
    'HCLTECH', 'TECHM', 'ULTRACEMCO', 'JSWSTEEL', 'ICICIBANK', 'POWERGRID',
    'DIVISLAB', 'NESTLEIND', 'BAJAJFINSV', 'TITAN', 'HINDUNILVR', 'INDIGO',
    'ADANIPORTS', 'BRITANNIA', 'COALINDIA', 'ONGC', 'GAIL', 'NTPC', 'BPCL',
    'HEROMOTOCO', 'SIEMENS', 'SHRIRAMFIN', 'TATACONSUM', 'TATASTEEL', 'EICHERMOT'
]

def log(message):
    """Print and log to file"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_message = f"[{timestamp}] {message}"
    print(log_message)

    # Append to log file
    try:
        os.makedirs('logs', exist_ok=True)
        with open('logs/nifty50_bot.log', 'a') as f:
            f.write(log_message + '\n')
    except:
        pass

def send_telegram_message(message):
    """Send message to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }
        response = requests.post(url, json=payload, timeout=10)
        success = response.json().get('ok', False)
        log(f"{'✅' if success else '❌'} Telegram sent")
        return success
    except Exception as e:
        log(f"❌ Telegram error: {e}")
        return False

def get_nifty50_signals():
    """Fetch NIFTY 50 signals"""
    signals = []

    try:
        log(f"Initializing PyZData with enctoken...")
        client = PyZData(enctoken=ZERODHA_ENCTOKEN)

        log(f"Fetching {len(NIFTY50_SYMBOLS)} stocks...")

        for symbol in NIFTY50_SYMBOLS:
            try:
                # Get instrument token
                token = client.get_instrument_token(symbol, "NSE")

                # Fetch 30-min data
                to_date = datetime.now()
                from_date = to_date - timedelta(days=5)

                data = client.get_data(
                    instrument_token=token,
                    start_date=from_date.strftime("%Y-%m-%d"),
                    end_date=to_date.strftime("%Y-%m-%d"),
                    interval=Interval.MINUTE_30
                )

                if data is None or len(data) < 2:
                    continue

                # Clean up
                data.columns = [col.lower() for col in data.columns]
                if 'datetime' in data.columns:
                    data['datetime'] = pd.to_datetime(data['datetime'])
                    data.set_index('datetime', inplace=True)

                # Set IST timezone
                if data.index.tz is None:
                    data.index = data.index.tz_localize('Asia/Kolkata')

                # Run UT Bot (Key Value=2, ATR Period=1)
                ut_signals = TradingViewIndicators.ut_bot_alert(
                    data['high'].values,
                    data['low'].values,
                    data['close'].values,
                    key_value=2,
                    atr_period=1
                )

                # Run LinReg (Signal Smoothing=6, SMA=ON, Length=8)
                linreg_signals = TradingViewIndicators.linear_reg_candles(
                    data['open'].values,
                    data['high'].values,
                    data['low'].values,
                    data['close'].values,
                    signal_length=6,
                    use_sma=True,
                    linreg_length=8
                )

                # Check latest bar
                latest_idx = len(data) - 1
                latest_close = data.iloc[latest_idx]['close']

                is_buy = ut_signals['buy_signal'][latest_idx]
                is_sell = ut_signals['sell_signal'][latest_idx]
                is_green = linreg_signals['green_candles'][latest_idx]
                is_red = linreg_signals['red_candles'][latest_idx]

                # Add signal if found
                if is_buy:
                    confidence = "HIGH" if is_green else "MEDIUM"
                    signals.append({
                        'symbol': symbol,
                        'type': 'BUY',
                        'price': latest_close,
                        'confidence': confidence
                    })
                    log(f"  🟢 {symbol} BUY")
                elif is_sell:
                    confidence = "HIGH" if is_red else "MEDIUM"
                    signals.append({
                        'symbol': symbol,
                        'type': 'SELL',
                        'price': latest_close,
                        'confidence': confidence
                    })
                    log(f"  🔴 {symbol} SELL")

            except Exception as e:
                # Skip individual stock errors
                continue

        return signals

    except Exception as e:
        log(f"❌ Error fetching signals: {e}")
        return []

def main():
    """Main entry point"""
    log("="*60)
    log("NIFTY50 SIGNAL BOT - LOCAL VERSION")
    log("="*60)

    try:
        # Validate credentials
        if not ZERODHA_ENCTOKEN:
            log("❌ ZERODHA_ENCTOKEN not found in .env_zerodha")
            return

        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            log("❌ Telegram credentials missing from .env")
            return

        log("✓ Credentials loaded")

        # Get signals
        log("\nFetching NIFTY50 signals...")
        signals = get_nifty50_signals()

        log(f"\nFound {len(signals)} signal(s)")

        # Format Telegram message
        if signals:
            message = f"<b>🚀 NIFTY50 SIGNALS</b>\n"
            message += f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M IST')}</i>\n\n"

            for sig in signals:
                emoji = "🟢" if sig['type'] == "BUY" else "🔴"
                message += f"{emoji} <b>{sig['symbol']}</b> {sig['type']}\n"
                message += f"   Price: ₹{sig['price']:.2f}\n"
                message += f"   Confidence: {sig['confidence']}\n\n"
        else:
            message = f"⚠️ No signals at {datetime.now().strftime('%H:%M IST')}"

        # Send to Telegram
        log("\nSending to Telegram...")
        send_telegram_message(message)

        log("\n✅ Bot execution complete")

    except Exception as e:
        log(f"❌ Error: {e}")
        import traceback
        log(traceback.format_exc())
        send_telegram_message(f"❌ Bot error: {str(e)[:100]}")

if __name__ == "__main__":
    main()
