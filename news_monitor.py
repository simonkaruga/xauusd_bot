import requests
from datetime import datetime, timedelta
from logger import logger

class NewsMonitor:
    def __init__(self):
        # Free API: https://nfs.faireconomy.media/ff_calendar_thisweek.json
        self.api_url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        self.high_impact_cache = []
        self.last_fetch = None
    
    def fetch_upcoming_events(self):
        """Fetch high-impact economic events"""
        try:
            response = requests.get(self.api_url, timeout=10)
            if response.status_code == 200:
                events = response.json()
                self.high_impact_cache = [
                    e for e in events 
                    if e.get('impact') == 'High' and 'USD' in e.get('country', '')
                ]
                self.last_fetch = datetime.now()
                logger.info(f"Fetched {len(self.high_impact_cache)} high-impact events")
                return True
        except Exception as e:
            logger.error(f"News fetch failed: {e}")
        return False
    
    def is_safe_to_trade(self):
        """Check if high-impact news is within 30 minutes"""
        if not self.high_impact_cache or not self.last_fetch:
            self.fetch_upcoming_events()
        
        # Refresh every hour
        if self.last_fetch and (datetime.now() - self.last_fetch).seconds > 3600:
            self.fetch_upcoming_events()
        
        now = datetime.now()
        
        for event in self.high_impact_cache:
            event_time = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
            time_diff = abs((event_time - now).total_seconds() / 60)
            
            if time_diff < 30:  # Within 30 minutes
                logger.warning(f"High-impact news in {time_diff:.0f} min: {event['title']}")
                return False
        
        return True
