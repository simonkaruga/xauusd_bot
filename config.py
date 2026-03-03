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
    
    # CONSERVATIVE Risk Management (Start Small)
    RISK_PER_TRADE = 0.01  # 1% instead of 2% - safer
    MAX_DAILY_LOSS = 0.03  # 3% instead of 6% - protect capital
    MAX_OPEN_TRADES = 1    # Only 1 trade at a time - focus
    MIN_RISK_REWARD = 2.0  # 1:2 R:R minimum - better trades only
    
    # Strategy Parameters (Proven settings)
    FAST_EMA = 9
    SLOW_EMA = 21
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    ATR_MULTIPLIER_SL = 2.0  # Wider SL - less stop-outs
    ATR_MULTIPLIER_TP = 4.0  # Bigger TP - better R:R
    
    # RSI Filters (Stricter - fewer but better signals)
    RSI_BUY_MIN = 45
    RSI_BUY_MAX = 65
    RSI_SELL_MIN = 35
    RSI_SELL_MAX = 55
    
    # Trading Session (GMT) - Best hours only
    TRADING_START_HOUR = 13  # 1 PM GMT (London/NY overlap)
    TRADING_END_HOUR = 16    # 4 PM GMT (3 hours only)
    
    # Logging
    LOG_LEVEL = 'INFO'
    LOG_FILE = 'logs/trading_bot.log'
    
    # Telegram (Optional)
    TELEGRAM_ENABLED = os.getenv('TELEGRAM_ENABLED', 'False').lower() == 'true'
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
    
    # News Filter - Avoid major news
    NEWS_BLACKOUT_PERIODS = [
        {'day': 'Friday', 'start': '12:30', 'end': '13:30', 'event': 'NFP'},
        {'day': 'Wednesday', 'start': '18:00', 'end': '19:00', 'event': 'FOMC'},
    ]
    
    # Multi-timeframe confirmation (Reduces false signals)
    HIGHER_TIMEFRAME = 'H1'
    USE_MTF_CONFIRMATION = True
    
    # Trailing Stop (Lock in profits)
    ENABLE_TRAILING_STOP = True
    BREAKEVEN_TRIGGER = 1.5  # Move to BE after 1.5x ATR profit
    TRAILING_DISTANCE = 1.0  # Trail by 1x ATR
    
    # SAFETY LIMITS
    MAX_TRADES_PER_DAY = 3   # Stop after 3 trades (prevent overtrading)
    MIN_TIME_BETWEEN_TRADES = 60  # 60 minutes cooldown
    
    # Volume Analysis (Advanced Features)
    USE_VOLUME_PROFILE = True  # Use volume profile for TP placement
    USE_DELTA_FILTER = True    # Require delta confirmation
    VOLUME_PROFILE_BINS = 20   # Number of price levels
    DELTA_THRESHOLD = 0.6      # 60% buy/sell pressure minimum
