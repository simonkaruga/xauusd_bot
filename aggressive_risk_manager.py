from datetime import datetime, date
from config import Config
from logger import logger

class AggressiveRiskManager:
    def __init__(self):
        self.daily_start_balance = 0
        self.peak_balance = 0
        self.current_date = date.today()
        self.circuit_breaker_active = False
        self.winning_streak = 0
        self.losing_streak = 0
        self.monthly_target_hit = False
    
    def calculate_dynamic_risk(self, account_balance, signal_confidence=0.7):
        """Increase risk on high confidence + winning streaks"""
        base_risk = Config.RISK_PER_TRADE
        
        # Confidence multiplier (0.6-1.0 confidence)
        confidence_multiplier = signal_confidence / 0.7
        
        # Winning streak bonus (max 2x)
        if self.winning_streak >= 3:
            streak_multiplier = min(1.5, 1 + (self.winning_streak * 0.1))
        else:
            streak_multiplier = 1.0
        
        # Reduce risk on losing streak
        if self.losing_streak >= 2:
            streak_multiplier = max(0.5, 1 - (self.losing_streak * 0.1))
        
        # Account growth multiplier (compound faster)
        if account_balance > self.daily_start_balance * 1.5:
            growth_multiplier = 1.3  # Increase risk when winning
        else:
            growth_multiplier = 1.0
        
        # Calculate final risk
        dynamic_risk = base_risk * confidence_multiplier * streak_multiplier * growth_multiplier
        
        # Cap at 5% max (aggressive but not suicidal)
        dynamic_risk = min(0.05, max(0.01, dynamic_risk))
        
        logger.info(f"Dynamic Risk: {dynamic_risk*100:.1f}% (Confidence: {signal_confidence}, Streak: W{self.winning_streak}/L{self.losing_streak})")
        
        return dynamic_risk
    
    def calculate_position_size(self, account_balance, entry_price, sl_price, signal_confidence=0.7, symbol_info=None):
        """Calculate position size with dynamic risk"""
        dynamic_risk = self.calculate_dynamic_risk(account_balance, signal_confidence)
        risk_amount = account_balance * dynamic_risk
        price_diff = abs(entry_price - sl_price)
        
        if price_diff == 0:
            return 0.01
        
        contract_size = 100
        if symbol_info:
            contract_size = symbol_info.get('trade_contract_size', 100)
        
        position_size = risk_amount / (price_diff * contract_size)
        
        if symbol_info:
            volume_min = symbol_info.get('volume_min', 0.01)
            volume_max = symbol_info.get('volume_max', 100)
            volume_step = symbol_info.get('volume_step', 0.01)
            
            position_size = max(volume_min, min(position_size, volume_max))
            position_size = round(position_size / volume_step) * volume_step
        else:
            position_size = max(0.01, round(position_size, 2))
        
        logger.info(f"Position: {position_size} lots (Risk: ${risk_amount:.2f} = {dynamic_risk*100:.1f}%)")
        return position_size
    
    def track_trade_result(self, profit):
        """Update streaks based on trade result"""
        if profit > 0:
            self.winning_streak += 1
            self.losing_streak = 0
            logger.info(f"✅ Win! Streak: {self.winning_streak}")
        else:
            self.losing_streak += 1
            self.winning_streak = 0
            logger.warning(f"❌ Loss! Streak: {self.losing_streak}")
    
    def should_take_profit_early(self, account_balance):
        """Lock in profits when monthly target hit"""
        monthly_gain = (account_balance - self.daily_start_balance) / self.daily_start_balance
        
        if monthly_gain >= 0.30:  # 30% monthly target
            self.monthly_target_hit = True
            logger.info(f"🎯 Monthly target hit: {monthly_gain*100:.1f}%")
            return True
        
        return False
    
    def can_trade(self, open_positions_count, account_balance):
        """More aggressive position limits"""
        if self.circuit_breaker_active:
            return False
        
        # Allow up to 3 positions if winning
        max_positions = 3 if self.winning_streak >= 2 else 2
        
        if open_positions_count >= max_positions:
            return False
        
        # Stop trading if monthly target hit (preserve gains)
        if self.monthly_target_hit:
            logger.info("Monthly target achieved - Trading paused")
            return False
        
        return True
