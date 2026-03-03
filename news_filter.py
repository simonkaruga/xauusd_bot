from datetime import datetime, time
from config import Config
from logger import logger

class NewsFilter:
    def __init__(self):
        self.blackout_periods = Config.NEWS_BLACKOUT_PERIODS
    
    def is_safe_to_trade(self):
        now = datetime.utcnow()
        current_time = now.time()
        current_day = now.strftime('%A')
        
        for period in self.blackout_periods:
            if period['day'] == current_day:
                start = time.fromisoformat(period['start'])
                end = time.fromisoformat(period['end'])
                
                if start <= current_time <= end:
                    logger.warning(f"Trading blocked - News blackout: {period['event']}")
                    return False
        
        return True
