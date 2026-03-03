import pandas as pd
import pandas_ta as ta
from datetime import datetime
from config import Config
from logger import logger
from volume_profile import VolumeProfile
from delta_analysis import DeltaAnalysis

class TrendFollowingStrategy:
    def __init__(self):
        self.name = "EMA_RSI_Trend"
        self.last_signal_time = None
        self.cooldown_minutes = 30
        self.volume_profile = VolumeProfile(bins=20)
        self.delta_analysis = DeltaAnalysis()
    
    def calculate_indicators(self, df):
        df['ema_fast'] = ta.ema(df['close'], length=Config.FAST_EMA)
        df['ema_slow'] = ta.ema(df['close'], length=Config.SLOW_EMA)
        df['rsi'] = ta.rsi(df['close'], length=Config.RSI_PERIOD)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=Config.ATR_PERIOD)
        return df
    
    def check_trading_session(self):
        current_hour = datetime.utcnow().hour
        return Config.TRADING_START_HOUR <= current_hour < Config.TRADING_END_HOUR
    
    def check_cooldown(self):
        if self.last_signal_time is None:
            return True
        
        minutes_passed = (datetime.now() - self.last_signal_time).total_seconds() / 60
        return minutes_passed >= self.cooldown_minutes
    
    def check_higher_timeframe(self, df_h1):
        if df_h1 is None or len(df_h1) < Config.SLOW_EMA:
            return True
        
        df_h1 = self.calculate_indicators(df_h1)
        current = df_h1.iloc[-1]
        
        return current['ema_fast'] > current['ema_slow']
    
    def generate_signal(self, df, df_h1=None):
        if len(df) < max(Config.SLOW_EMA, Config.RSI_PERIOD, Config.ATR_PERIOD) + 1:
            return None
        
        if not self.check_trading_session():
            return None
        
        if not self.check_cooldown():
            return None
        
        df = self.calculate_indicators(df)
        
        # Calculate volume profile for better TP placement
        vp = self.volume_profile.calculate(df)
        
        # Calculate delta for confirmation
        df = self.delta_analysis.calculate_delta(df)
        pressure = self.delta_analysis.get_pressure(df, lookback=10)
        
        current = df.iloc[-1]
        previous = df.iloc[-2]
        
        # BUY Signal
        if (previous['ema_fast'] <= previous['ema_slow'] and 
            current['ema_fast'] > current['ema_slow'] and
            Config.RSI_BUY_MIN <= current['rsi'] <= Config.RSI_BUY_MAX):
            
            if df_h1 is not None and not self.check_higher_timeframe(df_h1):
                logger.info("BUY signal rejected - H1 trend bearish")
                return None
            
            # Delta confirmation
            if not self.delta_analysis.confirm_signal('BUY', pressure):
                return None
            
            sl = current['close'] - (current['atr'] * Config.ATR_MULTIPLIER_SL)
            
            # Use volume profile for better TP
            if vp and Config.USE_VOLUME_PROFILE:
                hvn_target = self.volume_profile.get_nearest_hvn(current['close'], vp['hvn'], 'above')
                if hvn_target and hvn_target > current['close']:
                    tp = hvn_target
                    logger.info(f"TP adjusted to HVN: {tp:.2f}")
                else:
                    tp = current['close'] + (current['atr'] * Config.ATR_MULTIPLIER_TP)
            else:
                tp = current['close'] + (current['atr'] * Config.ATR_MULTIPLIER_TP)
            
            logger.info(f"BUY Signal - Price: {current['close']:.2f}, RSI: {current['rsi']:.2f}, Delta: {pressure['buy_pressure']*100:.1f}%")
            self.last_signal_time = datetime.now()
            
            return {
                'type': 'BUY',
                'price': current['close'],
                'sl': sl,
                'tp': tp,
                'atr': current['atr'],
                'vp': vp
            }
        
        # SELL Signal
        if (previous['ema_fast'] >= previous['ema_slow'] and 
            current['ema_fast'] < current['ema_slow'] and
            Config.RSI_SELL_MIN <= current['rsi'] <= Config.RSI_SELL_MAX):
            
            if df_h1 is not None and self.check_higher_timeframe(df_h1):
                logger.info("SELL signal rejected - H1 trend bullish")
                return None
            
            # Delta confirmation
            if not self.delta_analysis.confirm_signal('SELL', pressure):
                return None
            
            sl = current['close'] + (current['atr'] * Config.ATR_MULTIPLIER_SL)
            
            # Use volume profile for better TP
            if vp and Config.USE_VOLUME_PROFILE:
                hvn_target = self.volume_profile.get_nearest_hvn(current['close'], vp['hvn'], 'below')
                if hvn_target and hvn_target < current['close']:
                    tp = hvn_target
                    logger.info(f"TP adjusted to HVN: {tp:.2f}")
                else:
                    tp = current['close'] - (current['atr'] * Config.ATR_MULTIPLIER_TP)
            else:
                tp = current['close'] - (current['atr'] * Config.ATR_MULTIPLIER_TP)
            
            logger.info(f"SELL Signal - Price: {current['close']:.2f}, RSI: {current['rsi']:.2f}, Delta: {pressure['sell_pressure']*100:.1f}%")
            self.last_signal_time = datetime.now()
            
            return {
                'type': 'SELL',
                'price': current['close'],
                'sl': sl,
                'tp': tp,
                'atr': current['atr'],
                'vp': vp
            }
        
        return None
