from datetime import datetime, date
from config import Config
from logger import logger

class RiskManager:
    def __init__(self):
        self.daily_start_balance = 0
        self.daily_loss = 0
        self.current_date = date.today()
        self.circuit_breaker_active = False
    
    def reset_daily_tracking(self, current_balance):
        today = date.today()
        if today != self.current_date:
            self.current_date = today
            self.daily_start_balance = current_balance
            self.daily_loss = 0
            self.circuit_breaker_active = False
            logger.info(f"Daily tracking reset - Starting balance: ${current_balance:.2f}")
    
    def update_daily_loss(self, current_balance):
        if self.daily_start_balance == 0:
            self.daily_start_balance = current_balance
        
        loss = self.daily_start_balance - current_balance
        self.daily_loss = loss / self.daily_start_balance if self.daily_start_balance > 0 else 0
        
        if self.daily_loss >= Config.MAX_DAILY_LOSS:
            self.circuit_breaker_active = True
            logger.warning(f"CIRCUIT BREAKER ACTIVATED - Daily loss: {self.daily_loss*100:.2f}%")
    
    def can_trade(self, open_positions_count):
        if self.circuit_breaker_active:
            logger.warning("Trading blocked - Circuit breaker active")
            return False
        
        if open_positions_count >= Config.MAX_OPEN_TRADES:
            logger.info(f"Max open trades reached: {open_positions_count}")
            return False
        
        return True
    
    def calculate_position_size(self, account_balance, entry_price, sl_price):
        risk_amount = account_balance * Config.RISK_PER_TRADE
        price_diff = abs(entry_price - sl_price)
        
        if price_diff == 0:
            return 0.01
        
        # XAU/USD: 1 lot = 100 oz, pip value varies by price
        pip_value = 0.01  # Approximate for micro lots
        position_size = risk_amount / (price_diff * 100)
        
        # Round to 0.01 (micro lot)
        position_size = max(0.01, round(position_size, 2))
        
        logger.info(f"Position size: {position_size} lots (Risk: ${risk_amount:.2f})")
        return position_size
    
    def validate_risk_reward(self, entry_price, sl_price, tp_price):
        risk = abs(entry_price - sl_price)
        reward = abs(tp_price - entry_price)
        
        if risk == 0:
            return False
        
        rr_ratio = reward / risk
        
        if rr_ratio < Config.MIN_RISK_REWARD:
            logger.warning(f"R:R too low: {rr_ratio:.2f} (min: {Config.MIN_RISK_REWARD})")
            return False
        
        logger.info(f"R:R validated: {rr_ratio:.2f}")
        return True
