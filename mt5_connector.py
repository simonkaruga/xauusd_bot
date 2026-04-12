import MetaTrader5 as mt5
import pandas as pd
import time
from datetime import datetime, timedelta
from config import Config
from logger import logger

class MT5Connector:
    def __init__(self):
        self.connected = False
        self.max_retries = 3
        self.retry_delay = 5

    def connect(self):
        for attempt in range(self.max_retries):
            if not mt5.initialize():
                logger.error(f"MT5 init failed (attempt {attempt+1}): {mt5.last_error()}")
                time.sleep(self.retry_delay)
                continue

            if not mt5.login(Config.MT5_LOGIN, Config.MT5_PASSWORD, Config.MT5_SERVER):
                logger.error(f"MT5 login failed (attempt {attempt+1}): {mt5.last_error()}")
                mt5.shutdown()
                time.sleep(self.retry_delay)
                continue

            self.connected = True
            logger.info(f"Connected to MT5 - Account: {Config.MT5_LOGIN}, Server: {Config.MT5_SERVER}")
            return True

        logger.error("All MT5 connection attempts failed")
        return False

    def ensure_connection(self):
        if not self.connected or mt5.account_info() is None:
            logger.warning("Connection lost - reconnecting...")
            self.connected = False
            return self.connect()
        return True

    def disconnect(self):
        if self.connected:
            mt5.shutdown()
            self.connected = False
            logger.info("Disconnected from MT5")

    def get_bars(self, symbol, timeframe, count=100):
        if not self.ensure_connection():
            return None

        tf_map = {
            'M1': mt5.TIMEFRAME_M1, 'M5': mt5.TIMEFRAME_M5,
            'M15': mt5.TIMEFRAME_M15, 'M30': mt5.TIMEFRAME_M30,
            'H1': mt5.TIMEFRAME_H1, 'H4': mt5.TIMEFRAME_H4,
            'D1': mt5.TIMEFRAME_D1
        }

        for attempt in range(self.max_retries):
            rates = mt5.copy_rates_from_pos(symbol, tf_map.get(timeframe, mt5.TIMEFRAME_M15), 0, count)
            if rates is not None:
                df = pd.DataFrame(rates)
                df['time'] = pd.to_datetime(df['time'], unit='s')
                return df
            logger.warning(f"Failed to get bars (attempt {attempt+1}): {mt5.last_error()}")
            time.sleep(self.retry_delay)

        return None

    def get_account_info(self):
        if not self.ensure_connection():
            return None

        account = mt5.account_info()
        if account is None:
            return None

        return {
            'balance': account.balance,
            'equity': account.equity,
            'profit': account.profit,
            'margin_free': account.margin_free,
            'margin': account.margin
        }

    def get_symbol_info(self, symbol):
        if not self.ensure_connection():
            return None

        info = mt5.symbol_info(symbol)
        if info is None:
            return None

        return {
            'point': info.point,
            'digits': info.digits,
            'trade_contract_size': info.trade_contract_size,
            'volume_min': info.volume_min,
            'volume_max': info.volume_max,
            'volume_step': info.volume_step,
            'spread': info.spread
        }

    def get_positions(self, symbol=None):
        if not self.ensure_connection():
            return []

        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        return list(positions) if positions else []

    def get_history_deals(self, days=1):
        if not self.ensure_connection():
            return []

        from_date = datetime.now() - timedelta(days=days)
        deals = mt5.history_deals_get(from_date, datetime.now())
        return list(deals) if deals else []

    def place_order(self, symbol, order_type, volume, sl=0.0, tp=0.0, comment=""):
        if not self.ensure_connection():
            return None

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logger.error(f"Symbol {symbol} not found")
            return None

        if not symbol_info.visible:
            if not mt5.symbol_select(symbol, True):
                logger.error(f"Failed to select {symbol}")
                return None

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"Failed to get tick for {symbol}")
            return None

        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        for filling_mode in [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]:
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": 234000,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }

            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"Order placed: {order_type} {volume} {symbol} @ {price:.2f}")
                return result

            if result.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                logger.error(f"Order failed: {result.retcode} - {result.comment}")
                return None

        logger.error("Order failed with all filling modes")
        return None

    def close_position(self, ticket):
        if not self.ensure_connection():
            return False

        position = mt5.positions_get(ticket=ticket)
        if not position:
            return False

        pos = position[0]
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return False

        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        for filling_mode in [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]:
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": order_type,
                "position": ticket,
                "price": price,
                "deviation": 20,
                "magic": 234000,
                "comment": "Close position",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }

            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"Position closed: {ticket}")
                return True

            if result.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                logger.error(f"Close failed: {result.retcode} - {result.comment}")
                return False

        return False

    def modify_position_sl(self, ticket, new_sl):
        if not self.ensure_connection():
            return False

        position = mt5.positions_get(ticket=ticket)
        if not position:
            return False

        pos = position[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": new_sl,
            "tp": pos.tp
        }

        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"SL modified: {ticket} -> {new_sl:.2f}")
            return True

        logger.error(f"SL modify failed: {result.retcode}")
        return False
