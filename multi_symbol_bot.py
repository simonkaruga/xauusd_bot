from trading_bot import TradingBot
from config import Config
from logger import logger

class MultiSymbolBot:
    def __init__(self):
        self.symbols = {
            'XAUUSD': {'enabled': True, 'allocation': 0.50},
            'EURUSD': {'enabled': False, 'allocation': 0.25},
            'GBPUSD': {'enabled': False, 'allocation': 0.25}
        }
        self.bots = {}
    
    def initialize(self):
        """Create bot instance for each enabled symbol"""
        for symbol, config in self.symbols.items():
            if config['enabled']:
                bot = TradingBot()
                bot.symbol = symbol
                bot.risk_allocation = config['allocation']
                self.bots[symbol] = bot
                logger.info(f"Initialized bot for {symbol}")
    
    def run_cycle(self):
        """Execute trading cycle for all symbols"""
        for symbol, bot in self.bots.items():
            try:
                logger.info(f"Running cycle for {symbol}")
                bot.execute_trading_cycle()
            except Exception as e:
                logger.error(f"Error in {symbol} cycle: {e}")
    
    def get_total_exposure(self):
        """Calculate total risk across all symbols"""
        total_risk = 0
        for symbol, bot in self.bots.items():
            positions = bot.connector.get_positions(symbol)
            total_risk += len(positions) * Config.RISK_PER_TRADE
        return total_risk

# Usage:
# multi_bot = MultiSymbolBot()
# multi_bot.initialize()
# multi_bot.run_cycle()
