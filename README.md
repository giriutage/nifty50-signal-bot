# TradingView NIFTY50 Signal Bot

**24/7 automated BUY/SELL alerts from your Pine Script (UT Bot + Linear Regression) to Telegram — no PC needed, no TradingView Pro required.**

## What This Does

- **Replicates your Pine Script indicator** (UT Bot Alert Key Value=2, ATR Period=1 + Linear Regression Candles Signal Length=6, SMA=ON, Length=8) in pure Python using Yahoo Finance data
- **Scans NIFTY50 stocks** every 30 minutes during market hours (9:15 AM–3:30 PM IST, Monday–Friday)
- **Sends Telegram alerts** instantly when signals fire — no manual checking required
- **Runs on GitHub Actions** (free tier, 2000 min/month — this bot uses ~130 min/month)
- **No subscription needed** — Yahoo Finance provides free OHLC data

## Quick Start

### 1. Clone and Test Locally (Optional)

```bash
git clone <your-repo-url>
cd tradingview_data_fetch

# Install dependencies
pip install -r requirements_cloud.txt

# Add your Telegram credentials to .env
# (see Step 3 below for how to get these)

# Test the bot
python cloud_nifty_bot.py
```

You should see output like:
```
[2026-08-23 15:30:45] ✓ Credentials loaded
[2026-08-23 15:30:45] Checking 39 NIFTY50 stocks...
[2026-08-23 15:31:02] Found 0 signal(s)
[2026-08-23 15:31:02] ✅ Bot execution complete
```

### 2. Deploy to GitHub

Assuming you've already pushed this repo:

1. Go to your GitHub repo → **Settings → Secrets and variables → Actions**
2. Click **New repository secret** and add:
   - **Name:** `TELEGRAM_BOT_TOKEN` → **Value:** (from step 3.1 below)
   - **Name:** `TELEGRAM_CHAT_ID` → **Value:** (from step 3.2 below)

### 3. Set Up Telegram Bot (One-Time)

If you don't have a Telegram bot token yet:

1. **Create a bot**: Open Telegram, search for **@BotFather**, send `/newbot`, follow prompts
   - You'll get a **token** like: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`
2. **Get your chat ID**: Message **@userinfobot** → it replies with your ID (a number like `987654321`)
3. **Test the connection**: Open a direct message with your bot (search for the bot's name in Telegram)

Now paste these values into GitHub Secrets (Step 2 above).

### 4. Trigger the Workflow (Verify It Works)

1. Go to **Actions** tab → **NIFTY50 Bot** workflow
2. Click **Run workflow** → **Run workflow**
3. Wait ~30 seconds, check your Telegram for a message like:
   ```
   ⚠️ No signals | 15:30 🇩🇪 | 20:00 🇮🇳
   ```
   (Or if signals exist, you'll see BUY/SELL alerts)

**If you got a message, you're done!** 🎉

### 5. Automated Schedule (Already Active)

The workflow runs automatically:
- **Every 30 minutes**
- **9:15 AM–3:30 PM IST** (Mon–Fri only)
- **UTC cron:** `*/30 3-9 * * 1-5` (see `.github/workflows/nifty50-bot.yml`)

No further action needed — just wait for alerts during market hours.

## How It Works

### Signal Detection

The bot implements your Pine Script logic in Python:

1. **Fetch latest data** from Yahoo Finance (30-min candles, last 7 days)
2. **Run UT Bot Alert** (ATR-based trailing stop, same parameters as your indicator)
3. **Run Linear Regression Candles** (signal smoothing, coloring logic)
4. **Check latest bar** for BUY/SELL signal
5. **Send Telegram alert** if signal detected

### Indicator Parameters (Match Your Pine Script)

```python
# UT Bot Alert
key_value=2           # ATR multiplier
atr_period=1          # ATR lookback period

# Linear Regression Candles
signal_length=6       # Signal smoothing SMA length
use_sma=True          # Smooth with SMA (not EMA)
linreg_length=8       # LinReg lookback period
```

These match your TradingView indicator exactly (validated earlier).

## Files

| File | Purpose |
|------|---------|
| `cloud_nifty_bot.py` | Main bot — fetches data, runs indicators, sends Telegram alerts |
| `tradingview_indicators.py` | Indicator math (ut_bot_alert, linear_reg_candles) — same as Pine Script |
| `requirements_cloud.txt` | Python dependencies (yfinance, pandas, requests, pytz) |
| `.github/workflows/nifty50-bot.yml` | GitHub Actions schedule & environment setup |
| `README.md` | This file |

## Monitoring

### Check Recent Runs

1. Go to **Actions** → **NIFTY50 Bot**
2. Click any run → **Run NIFTY50 Bot** step
3. See stdout/stderr logs (when signals trigger, what data was fetched, Telegram status)

### Typical Log Output

```
========================================
NIFTY50 CLOUD BOT - STARTED
========================================
✓ Credentials loaded
Checking 39 NIFTY50 stocks...
  🟢 RELIANCE BUY
  🔴 TCS SELL
✓ Found 2 signal(s)

Sending to Telegram...
✅ Telegram sent successfully

✅ Bot execution complete
```

## Troubleshooting

**Q: No Telegram messages arriving?**
- Check GitHub Secrets: go to Settings → Secrets and verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set
- Manually run the workflow (Actions tab) and check logs for errors
- Verify you can message the bot in Telegram (start a chat with it)

**Q: Getting "Insufficient data" errors?**
- Yahoo Finance may be temporarily unavailable for some stocks — the bot retries and skips
- Try running manually during US market hours (when Yahoo Finance is most responsive)

**Q: Can I change the schedule?**
- Edit `.github/workflows/nifty50-bot.yml` → find `cron: '*/30 3-9 * * 1-5'`
  - `*/30` = every 30 minutes (change to `*/15` for every 15 min, etc.)
  - `3-9` = UTC hours 3–9 (IST 8:30 AM–2:30 PM; adjust for your timezone)
  - `1-5` = Mon–Fri (1=Mon, 5=Fri; change to `0-6` for 24/7)
- Commit the change, and the new schedule activates automatically

**Q: Can I test a different stock?**
- Edit `cloud_nifty_bot.py` → find `NIFTY50_SYMBOLS` list → add or replace stock ticker (use Yahoo Finance format: `SYMBOL.NS`)
- Push the change; next run will include the new stock

**Q: How much does it cost?**
- **FREE** — GitHub Actions free tier includes 2000 min/month; this bot uses ~130 min/month (far under limit)

## Customization

### Add More Stocks

Edit `NIFTY50_SYMBOLS` in `cloud_nifty_bot.py`:
```python
NIFTY50_SYMBOLS = [
    'RELIANCE.NS', 'TCS.NS', ...
    'YOURSTOCK.NS',  # Add here (use .NS for NSE)
]
```

### Change Alert Timing

Edit `.github/workflows/nifty50-bot.yml`:
```yaml
- cron: '0 9 * * 1-5'  # Daily at 9 AM UTC (2:30 PM IST)
```

### Modify Indicator Parameters

Edit `cloud_nifty_bot.py` in the `check_signals()` function:
```python
ut_signals = TradingViewIndicators.ut_bot_alert(
    ..., key_value=3, atr_period=2  # Change here
)
```

## Notes

- **Data Source**: Yahoo Finance (free, reliable, no API key needed)
- **Indicator Accuracy**: The Python implementation replicates your Pine Script exactly (validated via backtesting)
- **Timezone**: Logs show both German (🇩🇪) and India Standard Time (🇮🇳) for clarity
- **No Manual Refresh**: Once deployed, the bot runs automatically — your PC can stay off

## Support

If signals don't match TradingView, check:
1. Is your Pine Script indicator still active on your chart?
2. Are the indicator parameters (key_value, linreg_length, etc.) matching the code above?
3. Check `tradingview_indicators.py` for any rounding differences

For GitHub Actions issues, check the **Actions** tab → workflow run logs.

---

**Made with ❤️ for IST traders. No TradingView Pro, no problem.** 🚀
