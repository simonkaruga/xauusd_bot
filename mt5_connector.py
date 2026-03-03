import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
from config import Config
from logger import logger

class MT5Connector:
    def __init__(self):
        self.connected = False
        
    def connect(self):
        if not mt5.initialize():
            logger.error(f"MT5 initialization failed: {mt5.last_error()}")
            return False
        
        if not mt5.login(Config.MT5_LOGIN, Config.MT5_PASSWORD, Config.MT5_SERVER):
            logger.error(f"MT5 login failed: {mt5.last_error()}")
            mt5.shutdown()
            return False
        
        self.connected = True
        logger.info(f"Connected to MT5 - Account: {Config.MT5_LOGIN}, Server: {Config.MT5_SERVER}")
        return True
    
    def disconnect(self):
        if self.connected:
            mt5.shutdown()
            self.connected = False
            logger.info("Disconnected from MT5")
    
    def get_bars(self, symbol, timeframe, count=100):
        if not self.connected:
            return None
        
        tf_map = {
            'M1': mt5.TIMEFRAME_M1, 'M5': mt5.TIMEFRAME_M5,
            'M15': mt5.TIMEFRAME_M15, 'M30': mt5.TIMEFRAME_M30,
            'H1': mt5.TIMEFRAME_H1, 'H4': mt5.TIMEFRAME_H4,
            'D1': mt5.TIMEFRAME_D1
        }
        
        rates = mt5.copy_rates_from_pos(symbol, tf_map.get(timeframe, mt5.TIMEFRAME_M15), 0, count)
        if rates is None:
            logger.error(f"Failed to get bars: {mt5.last_error()}")
            return None
        
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df
    
    def get_account_info(self):
        if not self.connected:
            return None
        
        account = mt5.account_info()
        if account is None:
            return None
        
        return {
            'balance': account.balance,
            'equity': account.equity,
            'profit': account.profit,
            'margin_free': account.margin_free
        }
    
    def get_positions(self, symbol=None):
        if not self.connected:
            return []
        
        if symbol:
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()
        
        return list(positions) if positions else []
    
    def place_order(self, symbol, order_type, volume, sl=0.0, tp=0.0, comment=""):
        if not self.connected:
            return None
        
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            logger.error(f"Symbol {symbol} not found")
            return None
        
        if not symbol_info.visible:
            if not mt5.symbol_select(symbol, True):
                logger.error(f"Failed to select {symbol}")
                return None
        
        price = mt5.symbol_info_tick(symbol).ask if order_type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(symbol).bid
        
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
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {result.retcode} - {result.comment}")
            return None
        
        logger.info(f"Order placed: {order_type} {volume} {symbol} @ {price}")
        return result
    
    def close_position(self, ticket):
        if not self.connected:
            return False
        
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return False
        
        position = position[0]
        order_type = mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = mt5.symbol_info_tick(position.symbol).bid if position.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(position.symbol).ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000,
            "comment": "Close position",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Close failed: {result.retcode}")
            return False
        
        logger.info(f"Position closed: {ticket}")
        return True
