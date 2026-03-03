import MetaTrader5 as mt5
from datetime import datetime
from mt5_connector import MT5Connector
from strategy import TrendFollowingStrategy
from risk_manager import RiskManager
from kill_switch import KillSwitch
from execution_tracker import ExecutionTracker
from health_monitor import HealthMonitor
from news_monitor import NewsMonitor
from volatility_manager import VolatilityManager
from config import Config
from logger import logger

class TradingBot:
    def __init__(self):
        self.connector = MT5Connector()
        self.strategy = TrendFollowingStrategy()
        self.risk_manager = RiskManager()
        self.kill_switch = KillSwitch()
        self.execution_tracker = ExecutionTracker()
        self.health_monitor = HealthMonitor()
        self.news_monitor = NewsMonitor()
        self.volatility_manager = VolatilityManager()
        self.running = False
    
    def start(self):
        if not self.connector.connect():
            logger.error("Failed to connect to MT5")
            return False
        
        account_info = self.connector.get_account_info()
        if account_info:
            self.risk_manager.reset_daily_tracking(account_info['balance'])
            logger.info(f"Bot started - Balance: ${account_info['balance']:.2f}")
        
        self.running = True
        self.health_monitor.heartbeat()
        return True
    
    def stop(self):
        self.running = False
        self.connector.disconnect()
        logger.info("Bot stopped")
    
    def execute_trading_cycle(self):
        # Check kill switch
        if self.kill_switch.is_active():
            self.stop()
            return
        
        if self.kill_switch.is_paused():
            return
        
        if not self.running:
            return
        
        # Update heartbeat
        self.health_monitor.heartbeat()
        
        # Check health
        if not self.health_monitor.check_health():
            logger.error("Health check failed - attempting recovery")
            if not self.health_monitor.auto_recover():
                self.kill_switch.activate("Health check failed")
                return
        
        try:
            # Check news
            if not self.news_monitor.is_safe_to_trade():
                logger.warning("High-impact news detected - skipping cycle")
                return
            
            # Get account info
            account_info = self.connector.get_account_info()
            if not account_info:
                logger.error("Failed to get account info")
                self.health_monitor.record_error("Account info failed")
                return
            
            # Update risk tracking
            self.risk_manager.reset_daily_tracking(account_info['balance'])
            self.risk_manager.update_daily_loss(account_info['balance'])
            
            # Get open positions
            positions = self.connector.get_positions(Config.SYMBOL)
            open_count = len(positions)
            
            logger.info(f"Balance: ${account_info['balance']:.2f} | Open: {open_count} | Daily Loss: {self.risk_manager.daily_loss*100:.2f}%")
            
            # Check if we can trade
            if not self.risk_manager.can_trade(open_count):
                return
            
            # Get market data
            df = self.connector.get_bars(Config.SYMBOL, Config.TIMEFRAME, 100)
            if df is None or len(df) == 0:
                logger.error("Failed to get market data")
                self.health_monitor.record_error("Market data failed")
                return
            
            # Get higher timeframe
            df_h1 = None
            if Config.USE_MTF_CONFIRMATION:
                df_h1 = self.connector.get_bars(Config.SYMBOL, Config.HIGHER_TIMEFRAME, 50)
            
            # Generate signal
            signal = self.strategy.generate_signal(df, df_h1)
            if signal is None:
                return
            
            # Validate risk:reward
            if not self.risk_manager.validate_risk_reward(signal['price'], signal['sl'], signal['tp']):
                return
            
            # Get symbol info for accurate position sizing
            symbol_info = self.connector.get_symbol_info(Config.SYMBOL)
            
            # Adjust risk for volatility
            current_atr = signal['atr']
            avg_atr = df['atr'].mean() if 'atr' in df else current_atr
            adjusted_risk = self.volatility_manager.adjust_risk_for_volatility(current_atr, avg_atr)
            
            # Calculate position size with adjusted risk
            position_size = self.risk_manager.calculate_position_size(
                account_info['balance'],
                signal['price'],
                signal['sl'],
                symbol_info
            )
            
            # Adjust for volatility
            position_size = position_size * (adjusted_risk / Config.RISK_PER_TRADE)
            position_size = max(0.01, round(position_size, 2))
            
            # Close opposite positions
            for pos in positions:
                if (signal['type'] == 'BUY' and pos.type == mt5.ORDER_TYPE_SELL) or \
                   (signal['type'] == 'SELL' and pos.type == mt5.ORDER_TYPE_BUY):
                    self.connector.close_position(pos.ticket)
                    logger.info(f"Closed opposite position: {pos.ticket}")
            
            # Place order
            order_type = mt5.ORDER_TYPE_BUY if signal['type'] == 'BUY' else mt5.ORDER_TYPE_SELL
            
            # Get current price for slippage tracking
            tick = mt5.symbol_info_tick(Config.SYMBOL)
            intended_price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
            
            result = self.connector.place_order(
                Config.SYMBOL,
                order_type,
                position_size,
                sl=signal['sl'],
                tp=signal['tp'],
                comment=f"{self.strategy.name}"
            )
            
            if result:
                # Track execution quality
                executed_price = result.price
                self.execution_tracker.track_execution(intended_price, executed_price, signal['type'])
                
                logger.info(f"Trade executed: {signal['type']} {position_size} lots @ {executed_price:.2f}")
                
                # Reset error count on success
                self.health_monitor.reset_errors()
            else:
                logger.error("Failed to execute trade")
                self.health_monitor.record_error("Order execution failed")
        
        except Exception as e:
            logger.error(f"Error in trading cycle: {e}", exc_info=True)
            self.health_monitor.record_error(str(e))
    
    def run_once(self):
        """Execute one trading cycle - useful for scheduled runs"""
        self.execute_trading_cycle()
