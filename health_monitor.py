import time
from datetime import datetime, timedelta
from logger import logger

class HealthMonitor:
    def __init__(self):
        self.last_heartbeat = datetime.now()
        self.last_trade_check = datetime.now()
        self.error_count = 0
        self.max_errors = 5
        self.is_healthy = True
    
    def heartbeat(self):
        """Update heartbeat timestamp"""
        self.last_heartbeat = datetime.now()
    
    def check_health(self):
        """Check if bot is functioning properly"""
        now = datetime.now()
        
        # Check if heartbeat is recent (within 10 minutes)
        time_since_heartbeat = (now - self.last_heartbeat).total_seconds()
        
        if time_since_heartbeat > 600:  # 10 minutes
            logger.error(f"❌ Bot unresponsive for {time_since_heartbeat/60:.1f} minutes")
            self.is_healthy = False
            return False
        
        # Check error count
        if self.error_count >= self.max_errors:
            logger.error(f"❌ Too many errors: {self.error_count}")
            self.is_healthy = False
            return False
        
        self.is_healthy = True
        return True
    
    def record_error(self, error_msg):
        """Record an error occurrence"""
        self.error_count += 1
        logger.error(f"Error #{self.error_count}: {error_msg}")
        
        if self.error_count >= self.max_errors:
            logger.critical("🚨 Max errors reached - Bot needs attention")
    
    def reset_errors(self):
        """Reset error count after successful operation"""
        if self.error_count > 0:
            logger.info(f"✅ Errors cleared (was {self.error_count})")
        self.error_count = 0
    
    def get_status(self):
        """Get current health status"""
        return {
            'healthy': self.is_healthy,
            'last_heartbeat': self.last_heartbeat,
            'error_count': self.error_count,
            'uptime': (datetime.now() - self.last_heartbeat).total_seconds()
        }
    
    def auto_recover(self):
        """Attempt automatic recovery"""
        logger.warning("🔄 Attempting auto-recovery...")
        
        # Reset error count
        self.error_count = 0
        
        # Update heartbeat
        self.heartbeat()
        
        # Wait a bit
        time.sleep(5)
        
        logger.info("✅ Auto-recovery complete")
        return True
