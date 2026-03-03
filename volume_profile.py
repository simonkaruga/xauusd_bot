import pandas as pd
import numpy as np
from logger import logger

class VolumeProfile:
    def __init__(self, bins=20):
        self.bins = bins
    
    def calculate(self, df):
        """Calculate volume profile and identify key levels"""
        if len(df) < 50:
            return None
        
        price_min = df['low'].min()
        price_max = df['high'].max()
        price_range = price_max - price_min
        
        if price_range == 0:
            return None
        
        bin_size = price_range / self.bins
        volume_at_price = np.zeros(self.bins)
        
        # Distribute volume across price levels
        for _, row in df.iterrows():
            low_bin = int((row['low'] - price_min) / bin_size)
            high_bin = int((row['high'] - price_min) / bin_size)
            
            low_bin = max(0, min(low_bin, self.bins - 1))
            high_bin = max(0, min(high_bin, self.bins - 1))
            
            volume = row.get('tick_volume', 1)
            for i in range(low_bin, high_bin + 1):
                volume_at_price[i] += volume
        
        # Find key levels
        poc_idx = np.argmax(volume_at_price)  # Point of Control
        poc_price = price_min + (poc_idx * bin_size)
        
        # High Volume Nodes (top 3)
        hvn_indices = np.argsort(volume_at_price)[-3:]
        hvn_prices = [price_min + (i * bin_size) for i in hvn_indices]
        
        # Low Volume Nodes (bottom 3)
        lvn_indices = np.argsort(volume_at_price)[:3]
        lvn_prices = [price_min + (i * bin_size) for i in lvn_indices]
        
        return {
            'poc': poc_price,
            'hvn': sorted(hvn_prices),
            'lvn': sorted(lvn_prices),
            'value_area_high': price_min + (np.percentile(range(self.bins), 70, weights=volume_at_price) * bin_size),
            'value_area_low': price_min + (np.percentile(range(self.bins), 30, weights=volume_at_price) * bin_size)
        }
    
    def get_nearest_hvn(self, current_price, hvn_prices, direction='above'):
        """Find nearest high volume node for TP placement"""
        if direction == 'above':
            targets = [p for p in hvn_prices if p > current_price]
            return min(targets) if targets else None
        else:
            targets = [p for p in hvn_prices if p < current_price]
            return max(targets) if targets else None
    
    def get_nearest_lvn(self, current_price, lvn_prices, direction='above'):
        """Find nearest low volume node (price moves fast through these)"""
        if direction == 'above':
            targets = [p for p in lvn_prices if p > current_price]
            return min(targets) if targets else None
        else:
            targets = [p for p in lvn_prices if p < current_price]
            return max(targets) if targets else None
