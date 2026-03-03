# Quick Start Guide - Conservative Setup

## Step 1: Use Conservative Settings (5 minutes)

```bash
cd ~/xauusd_bot
cp config_conservative.py config.py
```

**What changed:**
- Risk: 2% → 1% (safer)
- Daily loss: 6% → 3% (tighter control)
- Max trades: 2 → 1 (focus on quality)
- R:R: 1:1.5 → 1:2 (better trades only)
- Trading hours: 4 hours → 3 hours (best time only)

---

## Step 2: Setup Demo Account (10 minutes)

### Get HFM Demo Account:
1. Go to https://www.hfm.com
2. Click "Open Demo Account"
3. Fill form:
   - Account Type: Standard
   - Platform: MetaTrader 5
   - Balance: $100
   - Leverage: 1:100

4. You'll receive email with:
   - Login: 12345678
   - Password: YourPass123
   - Server: HFM-Demo

### Configure Bot:
```bash
nano .env
```

Add your credentials:
```
MT5_LOGIN=12345678
MT5_PASSWORD=YourPass123
MT5_SERVER=HFM-Demo
```

Save: `Ctrl+X`, `Y`, `Enter`

---

## Step 3: Test Connection (2 minutes)

```bash
venv/bin/python -c "
from mt5_connector import MT5Connector
c = MT5Connector()
if c.connect():
    print('✅ Connected successfully!')
    info = c.get_account_info()
    print(f'Balance: \${info[\"balance\"]}')
else:
    print('❌ Connection failed')
"
```

**Expected output:**
```
✅ Connected successfully!
Balance: $100.00
```

---

## Step 4: Run Backtest (5 minutes)

```bash
venv/bin/python backtester.py
```

**Look for:**
```
=== BACKTEST RESULTS ===
Win Rate: 52.3% (15W / 13L)
Profit Factor: 1.45
Return: $12.50 (12.5%)
```

**Good if:**
- Win rate > 50%
- Profit factor > 1.3
- Positive return

**Bad if:**
- Win rate < 45%
- Profit factor < 1.0
- Negative return

---

## Step 5: Start Bot (1 minute)

```bash
# Start in background
nohup venv/bin/python main.py > bot.log 2>&1 &

# Check it's running
ps aux | grep main.py
```

**Or run in foreground (see live logs):**
```bash
venv/bin/python main.py
```

Press `Ctrl+C` to stop.

---

## Step 6: Monitor (Daily)

### Check Logs:
```bash
tail -f logs/trading_bot.log
```

### Check Trades:
```bash
sqlite3 logs/trades.db "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 5;"
```

### Check MT5:
- Open MT5 terminal
- Go to "Trade" tab
- See bot's trades

---

## What to Expect

### First 3 Days:
- Probably 0-1 trades (signals are rare with strict settings)
- This is GOOD - quality over quantity

### First Week:
- 2-4 trades expected
- 50-60% should be winners
- +$1 to +$3 profit (or small loss)

### First Month:
- 15-20 trades
- 50-55% win rate
- +$5 to +$10 profit

---

## Daily Routine

### Morning (9 AM):
```bash
# Check if bot is running
ps aux | grep main.py

# Check yesterday's trades
tail -20 logs/trading_bot.log
```

### During Trading (1-4 PM GMT):
- Bot runs automatically
- Check MT5 mobile app for trades
- Don't interfere

### Evening (6 PM):
```bash
# Review day's activity
grep "Trade executed" logs/trading_bot.log | tail -5
```

---

## Troubleshooting

### Bot not starting:
```bash
# Check errors
cat bot.log

# Restart
pkill -f main.py
nohup venv/bin/python main.py > bot.log 2>&1 &
```

### No trades executing:
- Check time (must be 1-4 PM GMT)
- Check logs for "No signal"
- This is normal - strict filters

### Connection lost:
- Bot auto-reconnects
- Check MT5 terminal is open
- Verify credentials in .env

---

## When to Go Live

### After 30 days, check:
```bash
# Calculate stats
venv/bin/python -c "
from trade_database import TradeDatabase
db = TradeDatabase()
trades = db.get_daily_trades()
print(f'Total trades: {len(trades)}')
"
```

**Go live if:**
- ✅ 15+ trades executed
- ✅ Win rate ≥ 50%
- ✅ No major bugs
- ✅ You understand the system

**Wait if:**
- ⚠️ < 15 trades (need more data)
- ⚠️ Win rate 40-50% (optimize first)
- ⚠️ Frequent errors

**Stop if:**
- ❌ Win rate < 40%
- ❌ Constant crashes
- ❌ Don't trust it

---

## Going Live Checklist

```
[ ] 30 days demo completed
[ ] Win rate ≥ 50%
[ ] Backtest positive
[ ] Understand every trade
[ ] Open HFM live account
[ ] Deposit $100
[ ] Update .env with live credentials
[ ] Start bot
[ ] Monitor daily for first week
```

---

## Support

**Check logs:**
```bash
tail -f logs/trading_bot.log
```

**Check database:**
```bash
sqlite3 logs/trades.db
.tables
SELECT * FROM trades;
.quit
```

**Restart bot:**
```bash
pkill -f main.py
venv/bin/python main.py
```

---

## Summary

1. ✅ Use conservative config
2. ✅ Setup demo account
3. ✅ Test connection
4. ✅ Run backtest
5. ✅ Start bot
6. ✅ Monitor daily
7. ✅ Go live after 30 days

**Start now. Stay safe. Build confidence.**
