"""
NIFTY50 Cloud Bot - Runs on Google Cloud Run / Railway
Monitors UT Bot + LinReg signals every 30 minutes
Sends alerts to Telegram (no PC needed)
"""

import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests
import json
from tradingview_indicators import TradingViewIndicators
import yfinance as yf
import pandas as pd

# Load Telegram credentials
load_dotenv('.env')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# NIFTY50 stocks
NIFTY50_SYMBOLS = [
    'RELIANCE.NS', 'TCS.NS', 'INFY.NS', 'HDFC.NS', 'WIPRO.NS', 'SBIN.NS', 'MARUTI.NS', 'BAJAJ-AUTO.NS',
    'LT.NS', 'AXISBANK.NS', 'BHARTIARTL.NS', 'ITC.NS', 'SUNPHARMA.NS', 'ASIANPAINT.NS',
    'HCLTECH.NS', 'TECHM.NS', 'ULTRACEMCO.NS', 'JSWSTEEL.NS', 'ICICIBANK.NS', 'POWERGRID.NS',
    'DIVISLAB.NS', 'NESTLEIND.NS', 'BAJAJFINSV.NS', 'TITAN.NS', 'HINDUNILVR.NS', 'INDIGO.NS',
    'ADANIPORTS.NS', 'BRITANNIA.NS', 'COALINDIA.NS', 'ONGC.NS', 'GAIL.NS', 'NTPC.NS', 'BPCL.NS',
    'HEROMOTOCO.NS', 'SIEMENS.NS', 'SHRIRAMFIN.NS', 'TATACONSUM.NS', 'TATASTEEL.NS', 'EICHERMOT.NS'
]

def log(message):
    """Log with timestamp"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"[{timestamp}] {message}"
    print(log_msg)

    # Also write to file for debugging
    try:
        with open('/tmp/cloud_bot.log', 'a') as f:
            f.write(log_msg + '\n')
    except:
        pass

def send_telegram(message):
    """Send message to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get('ok', False)
    except Exception as e:
        log(f"❌ Telegram error: {e}")
        return False

def get_stock_data(symbol, period='7d', interval='30m'):
    """Fetch stock data from Yahoo Finance"""
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if df is None or len(df) < 2:
            return None

        # Rename columns to lowercase
        df.columns = [col.lower() for col in df.columns]

        # Ensure required columns exist
        if not all(col in df.columns for col in ['open', 'high', 'low', 'close']):
            return None

        # Set IST timezone if not already set
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC').tz_convert('Asia/Kolkata')

        return df
    except Exception as e:
        log(f"❌ Error fetching {symbol}: {e}")
        return None

def check_signals(symbol, data):
    """Run indicators and check for signals"""
    try:
        # Run UT Bot Alert (Key Value=2, ATR Period=1)
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
        latest_time = data.index[latest_idx]

        is_buy = ut_signals['buy_signal'][latest_idx]
        is_sell = ut_signals['sell_signal'][latest_idx]
        is_green = linreg_signals['green_candles'][latest_idx]
        is_red = linreg_signals['red_candles'][latest_idx]

        # Return signal if found
        if is_buy:
            confidence = "HIGH" if is_green else "MEDIUM"
            return {
                'type': 'BUY',
                'price': latest_close,
                'time': latest_time,
                'confidence': confidence
            }
        elif is_sell:
            confidence = "HIGH" if is_red else "MEDIUM"
            return {
                'type': 'SELL',
                'price': latest_close,
                'time': latest_time,
                'confidence': confidence
            }

        return None

    except Exception as e:
        log(f"❌ Error checking signals for {symbol}: {e}")
        return None

def main():
    """Main bot logic"""
    log("="*60)
    log("NIFTY50 CLOUD BOT - STARTED")
    log("="*60)

    # Validate credentials
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("❌ Telegram credentials missing from .env")
        return

    log("✓ Credentials loaded")

    signals = []
    log(f"\nChecking {len(NIFTY50_SYMBOLS)} NIFTY50 stocks...")

    for symbol in NIFTY50_SYMBOLS:
        try:
            # Fetch data
            data = get_stock_data(symbol)
            if data is None:
                continue

            # Check for signals
            signal = check_signals(symbol, data)
            if signal:
                symbol_clean = symbol.replace('.NS', '')
                signals.append({
                    'symbol': symbol_clean,
                    'type': signal['type'],
                    'price': signal['price'],
                    'confidence': signal['confidence']
                })
                log(f"  {'🟢' if signal['type'] == 'BUY' else '🔴'} {symbol_clean} {signal['type']}")

        except Exception as e:
            continue

    log(f"\n✓ Found {len(signals)} signal(s)")

    # Format and send Telegram message
    import pytz
    now_ger = datetime.now()
    ist_tz = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(ist_tz)

    if signals:
        message = f"<b>🚀 NIFTY50 SIGNALS</b>\n"
        message += f"<i>{now_ger.strftime('%H:%M')} 🇩🇪 | {now_ist.strftime('%H:%M')} 🇮🇳</i>\n\n"

        for sig in signals:
            emoji = "🟢" if sig['type'] == "BUY" else "🔴"
            message += f"{emoji} <b>{sig['symbol']}</b> {sig['type']}\n"
            message += f"   Price: ₹{sig['price']:.2f}\n"
            message += f"   Confidence: {sig['confidence']}\n\n"
    else:
        message = f"⚠️ No signals | {now_ger.strftime('%H:%M')} 🇩🇪 | {now_ist.strftime('%H:%M')} 🇮🇳"

    # Send to Telegram
    log("\nSending to Telegram...")
    if send_telegram(message):
        log("✅ Telegram sent successfully")
    else:
        log("❌ Failed to send Telegram")

    log("\n✅ Bot execution complete")

if __name__ == "__main__":
    main()
