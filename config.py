import os
from dotenv import load_dotenv

load_dotenv()

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
    MIN_RISK_REWARD = 2.0        # 1:2 R:R minimum

    # Strategy Parameters
    FAST_EMA = 9
    SLOW_EMA = 21
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    ATR_MULTIPLIER_SL = 2.0
    ATR_MULTIPLIER_TP = 4.0

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

    # News Filter - All high impact USD events
    NEWS_BLACKOUT_PERIODS = [
        {'day': 'Friday',    'start': '12:00', 'end': '14:00', 'event': 'NFP'},
        {'day': 'Wednesday', 'start': '17:30', 'end': '19:30', 'event': 'FOMC'},
        {'day': 'Tuesday',   'start': '12:00', 'end': '13:00', 'event': 'CPI'},
        {'day': 'Thursday',  'start': '12:00', 'end': '13:00', 'event': 'GDP/Jobless'},
    ]

    # Multi-timeframe
    HIGHER_TIMEFRAME = 'H1'
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
