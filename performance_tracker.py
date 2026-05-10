import csv
import os
from datetime import datetime, timezone
from logger import logger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

class PerformanceTracker:
    def __init__(self):
        self.trades_file = 'logs/trades.csv'
        self.daily_file = 'logs/daily_stats.csv'
        self._init_files()
    
    def _init_files(self):
        os.makedirs('logs', exist_ok=True)
        
        if not os.path.exists(self.trades_file):
            with open(self.trades_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'type', 'symbol', 'entry', 'exit', 'sl', 'tp', 'volume', 'profit', 'balance'])
        
        if not os.path.exists(self.daily_file):
            with open(self.daily_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['date', 'trades', 'wins', 'losses', 'win_rate', 'profit', 'balance'])
    
    def log_trade(self, trade_type, symbol, entry, exit_price, sl, tp, volume, profit, balance):
        with open(self.trades_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                _utcnow().isoformat(),
                trade_type, symbol, entry, exit_price, sl, tp, volume, profit, balance
            ])
    
    def log_daily(self, trades, wins, losses, win_rate, profit, balance):
        with open(self.daily_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                _utcnow().date().isoformat(),
                trades, wins, losses, win_rate, profit, balance
            ])
