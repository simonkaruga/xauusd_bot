import pandas as pd
from logger import logger

class DeltaAnalysis:
    def __init__(self):
        self.delta_threshold = 0.6  # 60% buy pressure minimum
    
    def calculate_delta(self, df):
        """Calculate cumulative volume delta (buy vs sell pressure)"""
        if len(df) < 10:
            return None
        
        # Approximate buy/sell volume based on close vs open
        df['buy_volume'] = df.apply(
            lambda row: row.get('tick_volume', 1) if row['close'] > row['open'] else 0, 
            axis=1
        )
        df['sell_volume'] = df.apply(
            lambda row: row.get('tick_volume', 1) if row['close'] < row['open'] else 0, 
            axis=1
        )
        
        # Calculate delta
        df['delta'] = df['buy_volume'] - df['sell_volume']
        df['cumulative_delta'] = df['delta'].cumsum()
        
        return df
    
    def get_pressure(self, df, lookback=10):
        """Get current buying/selling pressure"""
        if len(df) < lookback:
            return None
        
        recent = df.tail(lookback)
        total_buy = recent['buy_volume'].sum()
        total_sell = recent['sell_volume'].sum()
        total_volume = total_buy + total_sell
        
        if total_volume == 0:
            return None
        
        buy_pressure = total_buy / total_volume
        
        return {
            'buy_pressure': buy_pressure,
            'sell_pressure': 1 - buy_pressure,
            'is_bullish': buy_pressure > self.delta_threshold,
            'is_bearish': buy_pressure < (1 - self.delta_threshold),
            'cumulative_delta': recent['cumulative_delta'].iloc[-1]
        }
    
    def confirm_signal(self, signal_type, pressure):
        """Confirm if signal aligns with volume pressure"""
        if pressure is None:
            return True  # No data, allow signal
        
        if signal_type == 'BUY':
            if pressure['is_bullish']:
                logger.info(f"✅ BUY confirmed by delta - Buy pressure: {pressure['buy_pressure']*100:.1f}%")
                return True
            else:
                logger.warning(f"⚠️ BUY rejected by delta - Buy pressure only: {pressure['buy_pressure']*100:.1f}%")
                return False
        
        elif signal_type == 'SELL':
            if pressure['is_bearish']:
                logger.info(f"✅ SELL confirmed by delta - Sell pressure: {pressure['sell_pressure']*100:.1f}%")
                return True
            else:
                logger.warning(f"⚠️ SELL rejected by delta - Sell pressure only: {pressure['sell_pressure']*100:.1f}%")
                return False
        
        return True
    
    def get_divergence(self, df, price_col='close'):
        """Detect price-delta divergence (early reversal signal)"""
        if len(df) < 20:
            return None
        
        recent = df.tail(20)
        
        # Price trend
        price_trend = recent[price_col].iloc[-1] > recent[price_col].iloc[0]
        
        # Delta trend
        delta_trend = recent['cumulative_delta'].iloc[-1] > recent['cumulative_delta'].iloc[0]
        
        # Divergence detection
        if price_trend and not delta_trend:
            return 'bearish_divergence'  # Price up, delta down = weakness
        elif not price_trend and delta_trend:
            return 'bullish_divergence'  # Price down, delta up = strength
        
        return None
