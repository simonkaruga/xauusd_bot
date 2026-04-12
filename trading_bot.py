import MetaTrader5 as mt5
from datetime import datetime
from mt5_connector import MT5Connector
from strategy import TrendFollowingStrategy
from risk_manager import RiskManager
from position_monitor import PositionMonitor
from kill_switch import KillSwitch
from execution_tracker import ExecutionTracker
from health_monitor import HealthMonitor
from news_monitor import NewsMonitor
from news_filter import NewsFilter
from volatility_manager import VolatilityManager
from telegram_notifier import TelegramNotifier
from trade_database import TradeDatabase
from config import Config
from logger import logger

class TradingBot:
    def __init__(self):
        self.connector = MT5Connector()
        self.strategy = TrendFollowingStrategy()
        self.risk_manager = RiskManager()
        self.position_monitor = PositionMonitor(self.connector)
        self.kill_switch = KillSwitch()
        self.execution_tracker = ExecutionTracker()
        self.health_monitor = HealthMonitor()
        self.news_monitor = NewsMonitor()
        self.news_filter = NewsFilter()
        self.volatility_manager = VolatilityManager()
        self.notifier = TelegramNotifier()
        self.db = TradeDatabase()
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
        # Safety checks first
        if self.kill_switch.is_active():
            self.stop()
            return

        if self.kill_switch.is_paused():
            return

        if not self.running:
            return

        self.health_monitor.heartbeat()

        if not self.health_monitor.check_health():
            if not self.health_monitor.auto_recover():
                self.kill_switch.activate("Health check failed")
                return

        try:
            # News checks
            if not self.news_filter.is_safe_to_trade():
                return

            if not self.news_monitor.is_safe_to_trade():
                return

            # Account info
            account_info = self.connector.get_account_info()
            if not account_info:
                self.health_monitor.record_error("Account info failed")
                return

            # Update risk tracking
            self.risk_manager.reset_daily_tracking(account_info['balance'])
            self.risk_manager.update_daily_loss(account_info['balance'])

            # Update trailing stops on open positions
            self.position_monitor.update_trailing_stops()

            # Get open positions
            positions = self.connector.get_positions(Config.SYMBOL)
            open_count = len(positions)

            logger.info(f"Balance: ${account_info['balance']:.2f} | Open: {open_count} | Daily Loss: {self.risk_manager.daily_loss*100:.2f}%")

            # Check trading conditions
            if not self.risk_manager.can_trade(open_count):
                return

            # Get market data
            df = self.connector.get_bars(Config.SYMBOL, Config.TIMEFRAME, 100)
            if df is None or len(df) == 0:
                self.health_monitor.record_error("Market data failed")
                return

            # Higher timeframe confirmation
            df_h1 = None
            if Config.USE_MTF_CONFIRMATION:
                df_h1 = self.connector.get_bars(Config.SYMBOL, Config.HIGHER_TIMEFRAME, 50)

            # Generate signal
            signal = self.strategy.generate_signal(df, df_h1)
            if signal is None:
                return

            # Validate R:R
            if not self.risk_manager.validate_risk_reward(signal['price'], signal['sl'], signal['tp']):
                return

            # Get symbol info for accurate sizing
            symbol_info = self.connector.get_symbol_info(Config.SYMBOL)

            # Adjust for volatility
            avg_atr = df['atr'].mean() if 'atr' in df.columns else signal['atr']
            adjusted_risk = self.volatility_manager.adjust_risk_for_volatility(signal['atr'], avg_atr)

            # Calculate position size
            position_size = self.risk_manager.calculate_position_size(
                account_info['balance'],
                signal['price'],
                signal['sl'],
                symbol_info
            )

            # Apply volatility adjustment
            position_size = max(0.01, round(position_size * (adjusted_risk / Config.RISK_PER_TRADE), 2))

            # Close opposite positions
            for pos in positions:
                if (signal['type'] == 'BUY' and pos.type == mt5.ORDER_TYPE_SELL) or \
                   (signal['type'] == 'SELL' and pos.type == mt5.ORDER_TYPE_BUY):
                    self.connector.close_position(pos.ticket)
                    self.position_monitor.untrack(pos.ticket)
                    logger.info(f"Closed opposite position: {pos.ticket}")

            # Place order
            order_type = mt5.ORDER_TYPE_BUY if signal['type'] == 'BUY' else mt5.ORDER_TYPE_SELL

            tick = mt5.symbol_info_tick(Config.SYMBOL)
            intended_price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

            result = self.connector.place_order(
                Config.SYMBOL,
                order_type,
                position_size,
                sl=signal['sl'],
                tp=signal['tp'],
                comment=self.strategy.name
            )

            if result:
                executed_price = result.price
                self.execution_tracker.track_execution(intended_price, executed_price, signal['type'])
                self.position_monitor.track(result.order, signal['atr'])
                self.risk_manager.track_trade(signal['type'], executed_price, position_size)
                self.db.insert_trade(result.order, Config.SYMBOL, signal['type'],
                                     executed_price, signal['sl'], signal['tp'], position_size)
                self.notifier.notify_trade(signal['type'], Config.SYMBOL, executed_price,
                                           signal['sl'], signal['tp'], position_size)
                self.health_monitor.reset_errors()
                logger.info(f"✅ Trade: {signal['type']} {position_size} lots @ {executed_price:.2f}")
            else:
                self.health_monitor.record_error("Order execution failed")
                self.notifier.notify_error("Order execution failed")

        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
            self.health_monitor.record_error(str(e))

    def run_once(self):
        self.execute_trading_cycle()
