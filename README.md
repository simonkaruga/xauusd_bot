# XAU/USD Institutional Trading Bot

Fully automated algorithmic trading bot for Gold/USD on MetaTrader 5.
Uses a 4-layer institutional signal hierarchy: macro conviction → key level → pullback → EMA timing.

## Architecture

```
Macro Gate (weekly/daily)
  └── COT commercial positioning + 10Y real yield + DXY composite score
  └── Only trades when macro score ≥ 0.40 — eliminates coin-flip neutral conditions

Key Level Gate (H4/daily)
  └── Entry must be at VWAP, volume profile HVN, or round-number level
  └── Mid-air entries are penalised in confidence but not hard-blocked

Pullback Confirmation (M15)
  └── Reversal candle at the key level
  └── RSI not overbought/oversold at entry
  └── Volume ≥ 90% of 20-bar average

EMA Timing (H4 + H1 + M15)
  └── All three timeframes must agree on direction
  └── Delta (buy vs sell pressure) confirmation
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
nano .env   # fill in MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
```

### 3. Train the ML signal classifier (first run only)

```bash
python train_from_backtest.py
```

This requires MT5 to be connected. It downloads historical data, runs the backtester,
and trains the GBM classifier that gates live signals. Output: `logs/signal_classifier.pkl`

### 4. Start the bot

```bash
python main.py
```

### Optional: Live dashboard

```bash
# Open http://localhost:5000 in a browser
# Dashboard auto-starts with the bot; or run standalone:
python dashboard.py
```

## Module Map

| File | Role |
|------|------|
| `main.py` | Event-driven bar-close loop, background threads, optimizer trigger |
| `trading_bot.py` | Main orchestrator — crash recovery, position restore |
| `advanced_strategy.py` | 4-layer institutional strategy (this file is the signal engine) |
| `risk_manager.py` | Kelly sizing, streak scaling, portfolio heat, circuit breaker |
| `macro_engine.py` | Real yield (FRED) + DXY + COT composite score |
| `news_sentiment.py` | Live NLP on RSS feeds — sentiment score fed to strategy |
| `ml_signal_classifier.py` | GBM model, PSI drift detection, pass-through when no model |
| `mt5_connector.py` | Full MT5 wrapper — limit orders, cancel, history |
| `position_monitor.py` | Trailing stop, breakeven, partial close, time stop |
| `trade_database.py` | SQLite with WAL mode, full trade attribution |
| `walk_forward_optimizer.py` | CPCV, Deflated Sharpe, config snapshot |
| `backtester.py` | M1 simulation, realistic costs, Monte Carlo |
| `correlation_filter.py` | USD proxy macro filter (EURUSD/DXY alignment) |
| `vwap_filter.py` | Daily VWAP + standard deviation bands |
| `volume_profile.py` | POC, HVN/LVN, value area — used for TP targeting |
| `cot_fetcher.py` | CFTC Commitment of Traders data, weekly cache |
| `health_monitor.py` | Real MT5 connectivity checks |
| `kill_switch.py` | File-based kill/pause (touch `KILL` to halt) |
| `dashboard.py` | Live Flask dashboard at port 5000 |
| `telegram_notifier.py` | Trade alerts via Telegram bot |
| `signal_broadcaster.py` | WebSocket broadcast for external consumers |

## Risk Settings (config.py)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_RISK_PER_TRADE` | 1% | Kelly-adjusted, based on `SIMULATED_BALANCE` |
| `MAX_DAILY_LOSS` | 3% | Circuit breaker threshold |
| `MAX_PORTFOLIO_HEAT` | 3% | Total open risk across all positions |
| `MIN_RISK_REWARD` | 2.0 | Minimum R:R to accept a signal |
| `SIMULATED_BALANCE` | $1,000 | Risk anchor (position sizing reference) |

## Trading Schedule

- Active hours configurable in `config.py` (`TRADING_START_HOUR` / `TRADING_END_HOUR`)
- Default: London/NY overlap (optimised via walk-forward analysis)
- Session guard: no entries in first/last 15 minutes of session
- Monday open and Friday close avoided

## Running Tests

```bash
pytest tests/ -v
```

The test suite covers RiskManager, TradeDatabase, CorrelationFilter, AdvancedStrategy, and Config validation (28 tests).

## Kill Switch

To halt trading immediately without restarting the process:

```bash
touch KILL        # stops new signals
touch PAUSE       # pauses without stopping (remove file to resume)
rm KILL           # resumes
```

## Platform Requirements

MetaTrader 5 runs on **Windows only**. Options:

- Windows desktop/laptop with MT5 installed
- Windows VPS (Vultr, AWS, etc.) — recommended for 24/7 operation
- Wine on Linux (limited support, not recommended for production)

## Safety

- Always start on a demo account
- Run `train_from_backtest.py` and review backtest stats before going live
- Monitor the dashboard for signal frequency — should be 0-3 trades/week at macro conviction threshold 0.40
- Never risk more than you can afford to lose

---

**Version:** 2.0 — Institutional Multi-Layer Strategy  
**Author:** Simon  
**License:** Personal Use Only
