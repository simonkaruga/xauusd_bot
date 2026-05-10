import os
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Strategy parameters that the walk-forward optimizer may override.
# ---------------------------------------------------------------------------
# Parameters the walk-forward optimizer may override.
# Only include params that advanced_strategy.py actually reads —
# RSI_BUY_MIN/MAX and RSI_SELL_MIN/MAX were removed because the active
# strategy uses hardcoded RSI ranges in _pullback_confirmed(), not Config values.
_OPTIMIZABLE = {
    'FAST_EMA':            int,
    'SLOW_EMA':            int,
    'ATR_MULTIPLIER_SL':   float,
    'ATR_MULTIPLIER_TP':   float,
    'ATR_MULTIPLIER_TP1':  float,
}


def _load_optimized_params():
    """
    If .env.optimized exists (written by WalkForwardOptimizer.apply_best_params),
    parse it and override the matching Config class attributes.
    Called once at import time so every module picks up the persisted params.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.optimized')
    if not os.path.exists(path):
        return

    loaded = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            if key in _OPTIMIZABLE and value.strip():
                try:
                    setattr(Config, key, _OPTIMIZABLE[key](value.strip()))
                    loaded[key] = value.strip()
                except ValueError:
                    pass  # skip malformed lines

    if loaded:
        # Lazy import to avoid circular dependency
        import logging
        logging.getLogger(__name__).info(
            f"config: loaded {len(loaded)} optimized param(s) from .env.optimized: {loaded}"
        )


class Config:
    # MT5 Connection
    MT5_LOGIN = int(os.getenv('MT5_LOGIN', 0))
    MT5_PASSWORD = os.getenv('MT5_PASSWORD', '')
    MT5_SERVER = os.getenv('MT5_SERVER', 'HFM-Demo')

    # Trading Parameters
    SYMBOL = 'XAUUSD'
    TIMEFRAME = 'M15'

    # ============================================================
    # DEMO TESTING - Simulating $1,000 real account on $100k demo
    # We only risk as if we have $1,000 (ignore the other $99,000)
    # This gives REALISTIC results matching your live account
    # ============================================================
    SIMULATED_BALANCE = 1000.0   # Treat as if you only have $1,000

    # Risk Management (based on SIMULATED_BALANCE, not $100k)
    RISK_PER_TRADE = 0.01        # 1% of $1,000 = $10 per trade
    MAX_DAILY_LOSS = 0.03        # 3% of $1,000 = $30 max daily loss
    MAX_OPEN_TRADES = 1          # 1 trade at a time
    MIN_RISK_REWARD = 2.5        # 1:2.5 R:R minimum (raised from 2.0)

    # Strategy Parameters
    FAST_EMA = 9
    SLOW_EMA = 21
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    ATR_MULTIPLIER_SL = 2.0
    ATR_MULTIPLIER_TP = 5.0      # 5R target on 2R stop = 2.5 R:R (matches MIN_RISK_REWARD)

    # RSI Filters
    RSI_BUY_MIN = 45
    RSI_BUY_MAX = 65
    RSI_SELL_MIN = 35
    RSI_SELL_MAX = 55

    # Trading Session (GMT) - London/NY Overlap ONLY (Best hours)
    TRADING_START_HOUR = 12      # 12 PM GMT = 3 PM Nairobi = 2 PM SA
    TRADING_END_HOUR = 16        # 4 PM GMT  = 7 PM Nairobi = 6 PM SA

    # Best trading days (Mon-Thu only, avoid Friday)
    TRADING_DAYS = 'mon-thu'     # Skip Friday (NFP risk, position squaring)

    # Logging
    LOG_LEVEL = 'INFO'
    LOG_FILE = 'logs/trading_bot.log'

    # Telegram
    TELEGRAM_ENABLED = os.getenv('TELEGRAM_ENABLED', 'False').lower() == 'true'
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

    # Signal broadcasting (optional — separate from personal alerts)
    SIGNAL_CHANNEL_ID = os.getenv('SIGNAL_CHANNEL_ID', '')   # e.g. @MySignalChannel
    SIGNAL_WEBHOOK_URL = os.getenv('SIGNAL_WEBHOOK_URL', '')  # copy-trading webhook

    # News Filter - All high impact USD events
    NEWS_BLACKOUT_PERIODS = [
        {'day': 'Friday',    'start': '12:00', 'end': '14:00', 'event': 'NFP'},
        {'day': 'Wednesday', 'start': '17:30', 'end': '19:30', 'event': 'FOMC'},
        {'day': 'Wednesday', 'start': '12:00', 'end': '13:00', 'event': 'CPI'},
        {'day': 'Tuesday',   'start': '12:00', 'end': '13:00', 'event': 'CPI'},
        {'day': 'Thursday',  'start': '12:00', 'end': '13:00', 'event': 'GDP/Jobless'},
    ]

    # Multi-timeframe — three-layer alignment: H4 + H1 + M15
    HIGHER_TIMEFRAME = 'H1'
    H4_TIMEFRAME = 'H4'
    USE_MTF_CONFIRMATION = True

    # Trailing Stop
    ENABLE_TRAILING_STOP = True
    BREAKEVEN_TRIGGER = 1.5
    TRAILING_DISTANCE = 1.0

    # Safety Limits
    MAX_TRADES_PER_DAY = 3
    MIN_TIME_BETWEEN_TRADES = 60

    # Volume Analysis
    USE_VOLUME_PROFILE = True
    USE_DELTA_FILTER = True
    VOLUME_PROFILE_BINS = 20
    DELTA_THRESHOLD = 0.6

    # Partial position scaling: close TP1_FRACTION at TP1, let rest run to TP2.
    # TP1 = entry + ATR_MULTIPLIER_TP1 × ATR  (lock in 2R)
    # TP2 = entry + ATR_MULTIPLIER_TP  × ATR  (original full target)
    USE_PARTIAL_CLOSE = True
    ATR_MULTIPLIER_TP1 = 2.0        # close TP1_FRACTION here
    TP1_FRACTION = 0.5              # close 50% of position at TP1

    # Time stop: close position if neither SL nor TP hit within N bars
    USE_TIME_STOP = True
    TIME_STOP_BARS = 8              # 8 × M15 = 2 hours of dead capital max

    # Limit order entry: post a limit instead of hitting the market.
    # Entry is placed ATR_LIMIT_OFFSET × ATR inside current price.
    # If not filled within LIMIT_ORDER_EXPIRY_BARS M15 bars, the order is cancelled.
    # Set USE_LIMIT_ORDERS=False to revert to market orders.
    USE_LIMIT_ORDERS = True
    ATR_LIMIT_OFFSET = 0.5          # 0.5 × ATR pullback for better fill price
    LIMIT_ORDER_EXPIRY_BARS = 1     # cancel after 1 bar (15 min) if unfilled

    # Session liquidity guard: skip first and last N minutes of each session.
    # Spreads are widest and algo activity highest at session boundaries.
    SESSION_GUARD_MINUTES = 15

    # Max drawdown from account peak before circuit breaker fires (10%)
    MAX_DRAWDOWN_FROM_PEAK = 0.10

    # Max total open risk across all positions as % of balance
    MAX_PORTFOLIO_HEAT = 0.03

    # Hard ceiling on risk per trade (safety net above Kelly)
    MAX_RISK_PER_TRADE = 0.02


# Apply any walk-forward optimized overrides saved in .env.optimized
_load_optimized_params()


def validate_config():
    """
    Call once at startup. Raises ValueError with a clear message if any
    required environment variable is missing or obviously wrong.
    """
    errors = []
    if not Config.MT5_LOGIN:
        errors.append("MT5_LOGIN is not set in .env")
    if not Config.MT5_PASSWORD:
        errors.append("MT5_PASSWORD is not set in .env")
    if not Config.MT5_SERVER:
        errors.append("MT5_SERVER is not set in .env")
    if Config.RISK_PER_TRADE <= 0 or Config.RISK_PER_TRADE > 0.10:
        errors.append(f"RISK_PER_TRADE={Config.RISK_PER_TRADE} is outside safe range (0, 0.10]")
    if Config.MAX_DAILY_LOSS <= 0 or Config.MAX_DAILY_LOSS > 0.20:
        errors.append(f"MAX_DAILY_LOSS={Config.MAX_DAILY_LOSS} is outside safe range (0, 0.20]")
    if errors:
        raise ValueError("Config validation failed:\n  " + "\n  ".join(errors))
