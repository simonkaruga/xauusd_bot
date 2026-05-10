from datetime import datetime, timezone
from logger import logger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

class ExecutionTracker:
    def __init__(self):
        self.executions = []
        self.slippage_threshold = 0.50  # $0.50 max acceptable slippage (XAU/USD price points)
    
    def track_execution(self, intended_price, executed_price, order_type):
        """Track slippage on each execution"""
        # Slippage in raw price points (XAU/USD quoted in USD per oz)
        slippage = abs(executed_price - intended_price)

        execution = {
            'timestamp': _utcnow(),
            'intended': intended_price,
            'executed': executed_price,
            'slippage': slippage,
            'type': order_type
        }

        self.executions.append(execution)

        if slippage > self.slippage_threshold:
            logger.warning(f"High slippage: {slippage:.2f} pts on {order_type}")
        else:
            logger.info(f"Execution OK: {slippage:.2f} pts slippage")

        return execution

    def get_avg_slippage(self, last_n=20):
        """Calculate average slippage in price points"""
        if not self.executions:
            return 0
        recent = self.executions[-last_n:]
        return sum(e['slippage'] for e in recent) / len(recent)

    def is_execution_quality_good(self):
        if len(self.executions) < 10:
            return True
        avg_slippage = self.get_avg_slippage()
        if avg_slippage > self.slippage_threshold:
            logger.error(f"Poor execution quality: {avg_slippage:.2f} pts avg slippage")
            return False
        return True

    def get_stats(self):
        if not self.executions:
            return None
        slippages = [e['slippage'] for e in self.executions]
        return {
            'total_executions': len(self.executions),
            'avg_slippage': round(sum(slippages) / len(slippages), 3),
            'max_slippage': round(max(slippages), 3),
            'min_slippage': round(min(slippages), 3),
        }
