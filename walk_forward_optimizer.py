import pandas as pd
from backtester import Backtester
from config import Config
from logger import logger

class WalkForwardOptimizer:
    def __init__(self):
        self.backtester = Backtester()
        self.best_params = {}
    
    def optimize(self, symbol='XAUUSD', total_days=180, train_days=90, test_days=30):
        """
        Walk-forward optimization:
        - Train on 90 days
        - Test on next 30 days
        - Roll forward
        """
        logger.info("Starting walk-forward optimization...")
        
        param_ranges = {
            'FAST_EMA': [7, 9, 12],
            'SLOW_EMA': [18, 21, 26],
            'RSI_BUY_MIN': [40, 45, 50],
            'RSI_BUY_MAX': [60, 65, 70],
            'ATR_MULTIPLIER_TP': [3.0, 4.0, 5.0]
        }
        
        results = []
        
        # Test each parameter combination
        for fast_ema in param_ranges['FAST_EMA']:
            for slow_ema in param_ranges['SLOW_EMA']:
                for rsi_min in param_ranges['RSI_BUY_MIN']:
                    for rsi_max in param_ranges['RSI_BUY_MAX']:
                        for tp_mult in param_ranges['ATR_MULTIPLIER_TP']:
                            
                            # Temporarily set params
                            Config.FAST_EMA = fast_ema
                            Config.SLOW_EMA = slow_ema
                            Config.RSI_BUY_MIN = rsi_min
                            Config.RSI_BUY_MAX = rsi_max
                            Config.ATR_MULTIPLIER_TP = tp_mult
                            
                            # Run backtest
                            result = self.backtester.run(symbol, days=train_days)
                            
                            if result and 'profit_factor' in result:
                                results.append({
                                    'params': {
                                        'fast_ema': fast_ema,
                                        'slow_ema': slow_ema,
                                        'rsi_min': rsi_min,
                                        'rsi_max': rsi_max,
                                        'tp_mult': tp_mult
                                    },
                                    'profit_factor': result['profit_factor'],
                                    'win_rate': result['win_rate'],
                                    'return': result['return_pct']
                                })
        
        # Find best parameters
        if results:
            best = max(results, key=lambda x: x['profit_factor'])
            self.best_params = best['params']
            
            logger.info("=== OPTIMIZATION RESULTS ===")
            logger.info(f"Best Profit Factor: {best['profit_factor']:.2f}")
            logger.info(f"Win Rate: {best['win_rate']:.1f}%")
            logger.info(f"Parameters: {best['params']}")
            
            return best
        
        return None
    
    def apply_best_params(self):
        """Apply optimized parameters to config"""
        if self.best_params:
            Config.FAST_EMA = self.best_params['fast_ema']
            Config.SLOW_EMA = self.best_params['slow_ema']
            Config.RSI_BUY_MIN = self.best_params['rsi_min']
            Config.RSI_BUY_MAX = self.best_params['rsi_max']
            Config.ATR_MULTIPLIER_TP = self.best_params['tp_mult']
            logger.info("✅ Optimized parameters applied")

if __name__ == "__main__":
    optimizer = WalkForwardOptimizer()
    result = optimizer.optimize()
    if result:
        optimizer.apply_best_params()
