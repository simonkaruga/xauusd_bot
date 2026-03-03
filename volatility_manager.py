from config import Config
from logger import logger

class VolatilityManager:
    def __init__(self):
        self.base_risk = Config.RISK_PER_TRADE
    
    def adjust_risk_for_volatility(self, current_atr, avg_atr):
        """Reduce position size when volatility spikes"""
        if avg_atr == 0:
            return self.base_risk
        
        volatility_ratio = current_atr / avg_atr
        
        # High volatility = reduce risk
        if volatility_ratio > 1.5:
            adjusted_risk = self.base_risk * 0.5  # 50% size
            logger.info(f"High volatility detected - Risk reduced to {adjusted_risk*100:.1f}%")
        elif volatility_ratio > 1.2:
            adjusted_risk = self.base_risk * 0.75  # 75% size
            logger.info(f"Elevated volatility - Risk reduced to {adjusted_risk*100:.1f}%")
        else:
            adjusted_risk = self.base_risk
        
        return adjusted_risk
