# 30-Day Conservative Testing Plan

## Goal: Prove the bot works before adding more money

---

## Week 1: Demo Account Setup

### Day 1-2: Installation & Configuration
```bash
# 1. Setup environment
cd ~/xauusd_bot
cp .env.example .env
nano .env  # Add HFM DEMO credentials

# 2. Use conservative config
cp config_conservative.py config.py

# 3. Test connection
venv/bin/python -c "from mt5_connector import MT5Connector; c = MT5Connector(); print('OK' if c.connect() else 'FAIL')"
```

### Day 3-4: Backtest
```bash
# Run 60-day backtest
venv/bin/python backtester.py

# Look for:
# - Win rate > 50%
# - Profit factor > 1.5
# - Max drawdown < 15%
```

### Day 5-7: Paper Trading
```bash
# Start bot on demo
venv/bin/python main.py

# Monitor:
# - Check logs every 4 hours
# - Verify signals make sense
# - No errors in execution
```

**Week 1 Target:** Bot runs without errors, backtest shows positive results

---

## Week 2-3: Demo Trading (Live Market)

### Daily Routine:
- **Morning:** Check overnight activity
- **1 PM GMT:** Bot starts scanning
- **4 PM GMT:** Trading window closes
- **Evening:** Review trades in logs

### Track These Metrics:
```
Date | Trades | Wins | Losses | P&L | Balance | Notes
-----|--------|------|--------|-----|---------|-------
Day 8|   1    |  1   |   0    | +$2 |  $102   | Good entry
Day 9|   0    |  -   |   -    |  $0 |  $102   | No signals
Day 10|  1    |  0   |   1    | -$1 |  $101   | Hit SL
...
```

### Success Criteria (Week 2-3):
- ✅ Win rate ≥ 50%
- ✅ No major bugs/crashes
- ✅ Average 2-3 trades per week
- ✅ Positive or break-even P&L
- ✅ Max drawdown < 10%

**Week 2-3 Target:** 10+ trades executed, system stable

---

## Week 4: Decision Week

### Calculate Results:
```python
# Run this after 3 weeks
venv/bin/python -c "
from trade_database import TradeDatabase
db = TradeDatabase()
trades = db.get_daily_trades()
wins = sum(1 for t in trades if t[10] > 0)  # profit column
total = len(trades)
print(f'Win Rate: {wins/total*100:.1f}%')
print(f'Total Trades: {total}')
"
```

### Decision Matrix:

**✅ GO LIVE IF:**
- Win rate ≥ 50%
- Profit factor ≥ 1.3
- At least 15 trades executed
- No critical bugs
- You understand how it works

**⚠️ OPTIMIZE IF:**
- Win rate 40-50%
- Profit factor 1.0-1.3
- Adjust RSI levels or timeframe
- Test another week

**❌ STOP IF:**
- Win rate < 40%
- Profit factor < 1.0
- Frequent errors
- Strategy doesn't fit market

---

## Going Live (After 30 Days)

### Pre-Live Checklist:
```
[ ] 30 days demo completed
[ ] Win rate ≥ 50%
[ ] Understand every trade
[ ] Telegram alerts working
[ ] Backup plan if bot fails
[ ] Comfortable with risk
```

### Live Account Setup:
```bash
# 1. Switch to live credentials
nano .env
# Change MT5_SERVER to HFM-Real
# Update login/password

# 2. Start with $100
# 3. Keep same conservative settings
# 4. Monitor DAILY for first week
```

### First Week Live:
- Check bot 3x per day
- Verify every trade manually
- Keep phone alerts on
- Don't change settings

---

## Monthly Review (After 30 Days Live)

### Calculate Performance:
```
Starting Balance: $100
Ending Balance: $___
Net Profit: $___
Win Rate: ___%
Total Trades: ___
Max Drawdown: ___%
```

### Decision Points:

**If Profitable (+$5 to +$15):**
- ✅ Continue with same settings
- ✅ Consider depositing $100 more
- ✅ Start tracking for signal service

**If Break-Even (±$2):**
- ⚠️ Run another month
- ⚠️ Minor optimizations
- ⚠️ Don't add money yet

**If Losing (-$5 to -$10):**
- ❌ Stop and analyze
- ❌ Review every trade
- ❌ Adjust strategy or quit

---

## Conservative Growth Plan

### Month 1: $100 → $105-110 (5-10%)
- Prove it works
- Build confidence
- Learn the system

### Month 2: $110 → $120-130 (10-20%)
- Add $100 deposit = $220
- Same settings
- More profit potential

### Month 3: $220 → $250-280 (15-25%)
- Add $100 deposit = $350
- Consider 1.5% risk
- Start signal channel

### Month 4-6: $350 → $500-700
- Compound profits
- Add deposits as comfortable
- Build audience

### Month 6+: Scale
- $1,000 account
- 2% risk
- Signal service income

---

## Daily Checklist

### Morning (Before Trading):
```
[ ] Check bot is running
[ ] Review yesterday's trades
[ ] Check for news events today
[ ] Verify MT5 connection
```

### During Trading (1-4 PM GMT):
```
[ ] Monitor Telegram alerts
[ ] Check if signals generated
[ ] Verify trades executed correctly
```

### Evening (After Trading):
```
[ ] Review day's performance
[ ] Update tracking spreadsheet
[ ] Check logs for errors
[ ] Plan for tomorrow
```

---

## Red Flags (Stop Immediately If):

1. **Technical Issues:**
   - Bot crashes repeatedly
   - Orders fail to execute
   - Connection drops frequently

2. **Performance Issues:**
   - 5 losses in a row
   - Daily loss > 3%
   - Win rate drops below 35%

3. **Market Issues:**
   - Extreme volatility (war, crisis)
   - Broker issues (slippage, requotes)
   - Your emotional stress

---

## Key Principles

1. **Start Small** - $100 is perfect for learning
2. **Be Patient** - 30 days minimum before live
3. **Track Everything** - Data drives decisions
4. **Don't Overtrade** - Quality > Quantity
5. **Protect Capital** - 1% risk, 3% daily max
6. **Stay Disciplined** - Don't override the bot
7. **Learn Continuously** - Understand every trade

---

## Expected Results (Realistic)

### Month 1 (Demo):
- 15-20 trades
- 50-55% win rate
- Break-even to +$5

### Month 2 (Live):
- 20-25 trades
- 50-60% win rate
- +$5 to +$10 profit

### Month 3 (Growing):
- 25-30 trades
- 55-60% win rate
- +$10 to +$20 profit

**After 3 months: $100 → $120-135 (20-35% return)**

Not life-changing, but PROOF it works.

Then you can scale with confidence.

---

## Next Steps

1. **Today:** Setup demo account
2. **This Week:** Run backtest + start demo
3. **Week 2-3:** Monitor and track
4. **Week 4:** Make go/no-go decision
5. **Month 2:** Go live if validated

**Remember: The goal isn't to get rich in 30 days.**
**The goal is to PROVE the system works, then scale.**

Start small. Stay safe. Build confidence.
