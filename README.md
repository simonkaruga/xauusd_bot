# XAU/USD Automated Trading Bot

Fully automated trading bot for Gold/USD on MetaTrader 5 using trend-following strategy.

## Features

- ✅ EMA Crossover (9/21) + RSI Filter Strategy
- ✅ Automated Risk Management (2% per trade)
- ✅ Daily Loss Circuit Breaker (6% max)
- ✅ Dynamic Stop-Loss/Take-Profit (ATR-based)
- ✅ Trading Session Control (London/NY Overlap)
- ✅ Comprehensive Logging

## Quick Start

### 1. Installation

```bash
# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your MT5 credentials
nano .env
```

### 3. Run Bot

```bash
# Start automated trading
python main.py
```

## Project Structure

```
xauusd_bot/
├── main.py              # Entry point with scheduler
├── trading_bot.py       # Main bot orchestrator
├── mt5_connector.py     # MT5 API wrapper
├── strategy.py          # Trading strategy logic
├── risk_manager.py      # Risk management system
├── config.py            # Configuration settings
├── logger.py            # Logging setup
├── requirements.txt     # Dependencies
├── .env                 # Credentials (create from .env.example)
└── logs/                # Trading logs (auto-created)
```

## Strategy Details

**Entry Signals:**
- BUY: Fast EMA crosses above Slow EMA + RSI 40-70
- SELL: Fast EMA crosses below Slow EMA + RSI 30-60

**Exit:**
- Take-Profit: 2x ATR
- Stop-Loss: 1.5x ATR

**Risk Management:**
- 2% risk per trade
- Max 2 open positions
- 6% daily loss limit (circuit breaker)

## Trading Schedule

- **Active Hours:** 12:00-16:00 GMT (Mon-Fri)
- **Check Interval:** Every 5 minutes
- **Symbol:** XAU/USD

## Safety Features

1. **Circuit Breaker:** Stops trading if daily loss hits 6%
2. **Position Limits:** Maximum 2 concurrent trades
3. **Risk:Reward Validation:** Minimum 1:1.5 R:R required
4. **Session Control:** Only trades during optimal hours

## Monitoring

- All trades visible in MT5 terminal
- Detailed logs in `logs/trading_bot.log`
- Real-time monitoring via MT5 mobile app

## Testing Workflow

1. **Demo Testing (2 weeks minimum)**
   - Use HFM demo account
   - Monitor performance daily
   - Validate strategy effectiveness

2. **Live Deployment**
   - Start with 0.01 lot size
   - Gradually increase confidence
   - Consider VPS for 24/7 uptime

## Configuration Options

Edit `config.py` to customize:
- Strategy parameters (EMA periods, RSI thresholds)
- Risk settings (risk per trade, max loss)
- Trading hours
- Position sizing

## Troubleshooting

**Connection Issues:**
- Verify MT5 credentials in `.env`
- Ensure MT5 terminal is running
- Check server name (HFM-Demo or HFM-Real)

**No Trades Executing:**
- Confirm trading hours (GMT timezone)
- Check if circuit breaker is active
- Review logs for signal generation

**Position Sizing:**
- Default: 0.01 lots (micro)
- Automatically calculated based on 2% risk
- Adjust in risk_manager.py if needed

## Important Notes

⚠️ **Start with demo account**
⚠️ **Never risk more than you can afford to lose**
⚠️ **Past performance doesn't guarantee future results**

## Support

For issues or questions:
1. Check logs in `logs/trading_bot.log`
2. Review MT5 terminal for trade details
3. Verify configuration in `.env` and `config.py`

---

**Version:** 1.0  
**Author:** Kelvin  
**License:** Personal Use Only
