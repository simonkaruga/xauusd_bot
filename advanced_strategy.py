import pandas as pd
import pandas_ta as ta
from datetime import datetime
from config import Config
from logger import logger

class AdvancedStrategy:
    def __init__(self):
        self.name = "Multi_Strategy_System"
        self.strategies = {
            'trend': self.trend_strategy,
            'breakout': self.breakout_strategy,
            'reversal': self.reversal_strategy
        }
        self.market_regime = 'trending'
    
    def detect_market_regime(self, df):
        """Detect if market is trending or ranging"""
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        current_adx = adx['ADX_14'].iloc[-1]
        
        if current_adx > 25:
            return 'trending'
        elif current_adx < 20:
            return 'ranging'
        else:
            return 'neutral'
    
    def trend_strategy(self, df):
        """Original EMA strategy - works in trends"""
        df['ema_fast'] = ta.ema(df['close'], length=9)
        df['ema_slow'] = ta.ema(df['close'], length=21)
        df['rsi'] = ta.rsi(df['close'], length=14)
        
        current = df.iloc[-1]
        previous = df.iloc[-2]
        
        if (previous['ema_fast'] <= previous['ema_slow'] and 
            current['ema_fast'] > current['ema_slow'] and
            40 <= current['rsi'] <= 70):
            return {'type': 'BUY', 'confidence': 0.7}
        
        if (previous['ema_fast'] >= previous['ema_slow'] and 
            current['ema_fast'] < current['ema_slow'] and
            30 <= current['rsi'] <= 60):
            return {'type': 'SELL', 'confidence': 0.7}
        
        return None
    
    def breakout_strategy(self, df):
        """Trade breakouts of consolidation - high win rate"""
        df['bb_upper'] = ta.bbands(df['close'], length=20)['BBU_20_2.0']
        df['bb_lower'] = ta.bbands(df['close'], length=20)['BBL_20_2.0']
        df['volume_ma'] = df['tick_volume'].rolling(20).mean() if 'tick_volume' in df else 1
        
        current = df.iloc[-1]
        previous = df.iloc[-2]
        
        # Breakout above resistance with volume
        if (previous['close'] <= previous['bb_upper'] and 
            current['close'] > current['bb_upper']):
            return {'type': 'BUY', 'confidence': 0.8}
        
        # Breakdown below support
        if (previous['close'] >= previous['bb_lower'] and 
            current['close'] < current['bb_lower']):
            return {'type': 'SELL', 'confidence': 0.8}
        
        return None
    
    def reversal_strategy(self, df):
        """Mean reversion in ranging markets"""
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['bb_upper'] = ta.bbands(df['close'], length=20)['BBU_20_2.0']
        df['bb_lower'] = ta.bbands(df['close'], length=20)['BBL_20_2.0']
        
        current = df.iloc[-1]
        
        # Oversold reversal
        if current['rsi'] < 25 and current['close'] <= current['bb_lower']:
            return {'type': 'BUY', 'confidence': 0.6}
        
        # Overbought reversal
        if current['rsi'] > 75 and current['close'] >= current['bb_upper']:
            return {'type': 'SELL', 'confidence': 0.6}
        
        return None
    
    def generate_signal(self, df):
        """Select best strategy based on market regime"""
        if len(df) < 50:
            return None
        
        # Detect market regime
        self.market_regime = self.detect_market_regime(df)
        
        # Use appropriate strategy
        if self.market_regime == 'trending':
            signal = self.trend_strategy(df)
        elif self.market_regime == 'ranging':
            signal = self.reversal_strategy(df)
        else:
            # Try both, take highest confidence
            trend_sig = self.trend_strategy(df)
            breakout_sig = self.breakout_strategy(df)
            
            if trend_sig and breakout_sig:
                signal = trend_sig if trend_sig['confidence'] > breakout_sig['confidence'] else breakout_sig
            else:
                signal = trend_sig or breakout_sig
        
        if signal:
            # Calculate SL/TP
            atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
            current_price = df.iloc[-1]['close']
            
            if signal['type'] == 'BUY':
                sl = current_price - (atr * 1.5)
                tp = current_price + (atr * 3.0)  # Increased R:R
            else:
                sl = current_price + (atr * 1.5)
                tp = current_price - (atr * 3.0)
            
            signal.update({
                'price': current_price,
                'sl': sl,
                'tp': tp,
                'atr': atr,
                'regime': self.market_regime
            })
            
            logger.info(f"{signal['type']} Signal - Regime: {self.market_regime}, Confidence: {signal['confidence']}")
        
        return signal
