from datetime import datetime
from logger import logger

class ExecutionTracker:
    def __init__(self):
        self.executions = []
        self.slippage_threshold = 5.0  # 5 pips max acceptable
    
    def track_execution(self, intended_price, executed_price, order_type):
        """Track slippage on each execution"""
        slippage = abs(executed_price - intended_price)
        slippage_pips = slippage * 10  # For XAU/USD
        
        execution = {
            'timestamp': datetime.now(),
            'intended': intended_price,
            'executed': executed_price,
            'slippage': slippage,
            'slippage_pips': slippage_pips,
            'type': order_type
        }
        
        self.executions.append(execution)
        
        if slippage_pips > self.slippage_threshold:
            logger.warning(f"⚠️ High slippage: {slippage_pips:.1f} pips on {order_type}")
        else:
            logger.info(f"✅ Execution OK: {slippage_pips:.1f} pips slippage")
        
        return execution
    
    def get_avg_slippage(self, last_n=20):
        """Calculate average slippage"""
        if not self.executions:
            return 0
        
        recent = self.executions[-last_n:]
        avg_slippage = sum(e['slippage_pips'] for e in recent) / len(recent)
        
        return avg_slippage
    
    def is_execution_quality_good(self):
        """Check if broker execution is acceptable"""
        if len(self.executions) < 10:
            return True
        
        avg_slippage = self.get_avg_slippage()
        
        if avg_slippage > self.slippage_threshold:
            logger.error(f"❌ Poor execution quality: {avg_slippage:.1f} pips avg slippage")
            return False
        
        return True
    
    def get_stats(self):
        """Get execution statistics"""
        if not self.executions:
            return None
        
        slippages = [e['slippage_pips'] for e in self.executions]
        
        return {
            'total_executions': len(self.executions),
            'avg_slippage': sum(slippages) / len(slippages),
            'max_slippage': max(slippages),
            'min_slippage': min(slippages)
        }
