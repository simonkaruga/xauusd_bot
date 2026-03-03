from datetime import datetime, timedelta
from trade_database import TradeDatabase
from logger import logger

class PerformanceMonitor:
    def __init__(self):
        self.db = TradeDatabase()
        self.alerts = []
    
    def calculate_sharpe_ratio(self, returns):
        """Calculate risk-adjusted returns"""
        if len(returns) < 2:
            return 0
        
        import numpy as np
        mean_return = np.mean(returns)
        std_return = np.std(returns)
        
        if std_return == 0:
            return 0
        
        sharpe = (mean_return / std_return) * (252 ** 0.5)  # Annualized
        return sharpe
    
    def check_performance_degradation(self):
        """Alert if performance drops significantly"""
        trades_30d = self.db.get_daily_trades()
        
        if len(trades_30d) < 20:
            return None
        
        recent_10 = trades_30d[-10:]
        previous_10 = trades_30d[-20:-10]
        
        recent_wins = sum(1 for t in recent_10 if t[10] > 0)
        previous_wins = sum(1 for t in previous_10 if t[10] > 0)
        
        recent_wr = recent_wins / 10
        previous_wr = previous_wins / 10
        
        if recent_wr < previous_wr - 0.15:  # 15% drop
            alert = f"⚠️ Win rate dropped from {previous_wr*100:.0f}% to {recent_wr*100:.0f}%"
            logger.warning(alert)
            self.alerts.append(alert)
            return alert
        
        return None
    
    def get_metrics(self):
        """Calculate all performance metrics"""
        trades = self.db.get_daily_trades()
        
        if not trades:
            return None
        
        profits = [t[10] for t in trades if t[10] is not None]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]
        
        total = len(profits)
        win_count = len(wins)
        
        metrics = {
            'total_trades': total,
            'win_rate': (win_count / total * 100) if total > 0 else 0,
            'profit_factor': (sum(wins) / abs(sum(losses))) if losses else 0,
            'avg_win': (sum(wins) / len(wins)) if wins else 0,
            'avg_loss': (sum(losses) / len(losses)) if losses else 0,
            'sharpe_ratio': self.calculate_sharpe_ratio(profits),
            'expectancy': sum(profits) / total if total > 0 else 0
        }
        
        return metrics
    
    def daily_report(self):
        """Generate daily performance report"""
        metrics = self.get_metrics()
        
        if not metrics:
            return "No trades yet"
        
        report = f"""
📊 PERFORMANCE REPORT
━━━━━━━━━━━━━━━━━━━━
Total Trades: {metrics['total_trades']}
Win Rate: {metrics['win_rate']:.1f}%
Profit Factor: {metrics['profit_factor']:.2f}
Sharpe Ratio: {metrics['sharpe_ratio']:.2f}
Expectancy: ${metrics['expectancy']:.2f}
Avg Win: ${metrics['avg_win']:.2f}
Avg Loss: ${metrics['avg_loss']:.2f}
        """
        
        logger.info(report)
        return report
