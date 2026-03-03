import os
from datetime import datetime
from logger import logger

class KillSwitch:
    def __init__(self):
        self.kill_file = 'KILL_SWITCH.txt'
        self.pause_file = 'PAUSE_TRADING.txt'
    
    def is_active(self):
        """Check if kill switch is activated"""
        if os.path.exists(self.kill_file):
            logger.critical("🚨 KILL SWITCH ACTIVATED - Bot stopped")
            return True
        return False
    
    def is_paused(self):
        """Check if trading is paused"""
        if os.path.exists(self.pause_file):
            logger.warning("⏸️ Trading paused")
            return True
        return False
    
    def activate(self, reason="Manual activation"):
        """Activate kill switch"""
        with open(self.kill_file, 'w') as f:
            f.write(f"Activated: {datetime.now()}\nReason: {reason}\n")
        logger.critical(f"🚨 Kill switch activated: {reason}")
    
    def deactivate(self):
        """Deactivate kill switch"""
        if os.path.exists(self.kill_file):
            os.remove(self.kill_file)
            logger.info("✅ Kill switch deactivated")
    
    def pause(self):
        """Pause trading temporarily"""
        with open(self.pause_file, 'w') as f:
            f.write(f"Paused: {datetime.now()}\n")
        logger.warning("⏸️ Trading paused")
    
    def resume(self):
        """Resume trading"""
        if os.path.exists(self.pause_file):
            os.remove(self.pause_file)
            logger.info("▶️ Trading resumed")

# Usage:
# To stop bot: touch KILL_SWITCH.txt
# To pause: touch PAUSE_TRADING.txt
# To resume: rm PAUSE_TRADING.txt
