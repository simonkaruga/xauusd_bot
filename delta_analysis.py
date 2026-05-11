"""
Volume Delta Analysis
======================
Approximates order-flow pressure from OHLCV bars using the close-position
method: a bar closing near its high indicates dominant buying; near its low
indicates dominant selling. This is significantly more accurate than the
binary close>open split because it captures partial pressure (e.g., a bullish
bar that closes mid-range has lower net buy pressure than one that closes at
the high).

Formula per bar:
  close_pos  = (close - low) / (high - low + ε)   # 0=closed at low, 1=at high
  buy_volume  = tick_volume × close_pos
  sell_volume = tick_volume × (1 − close_pos)
  delta       = buy_volume − sell_volume

Cumulative delta divergence from price = early reversal warning.
"""

import numpy as np
import pandas as pd
from logger import logger


class DeltaAnalysis:
    def __init__(self):
        self.delta_threshold = 0.60   # 60% buy pressure required to confirm BUY

    def calculate_delta(self, df: pd.DataFrame) -> pd.DataFrame | None:
        if len(df) < 10:
            return None
        df = df.copy()

        bar_range = (df['high'] - df['low']).clip(lower=1e-9)
        close_pos = (df['close'] - df['low']) / bar_range   # [0, 1]

        df['buy_volume']  = df['tick_volume'] * close_pos
        df['sell_volume'] = df['tick_volume'] * (1.0 - close_pos)
        df['delta']            = df['buy_volume'] - df['sell_volume']
        df['cumulative_delta'] = df['delta'].cumsum()

        # 5-bar smoothed delta momentum (positive = rising buy pressure)
        df['delta_momentum'] = df['delta'].rolling(5, min_periods=1).mean()

        return df

    def get_pressure(self, df: pd.DataFrame, lookback: int = 10) -> dict | None:
        if df is None or len(df) < lookback:
            return None
        if 'buy_volume' not in df.columns:
            return None

        recent = df.tail(lookback)
        total_buy  = float(recent['buy_volume'].sum())
        total_sell = float(recent['sell_volume'].sum())
        total_vol  = total_buy + total_sell
        if total_vol == 0:
            return None

        buy_pressure = total_buy / total_vol
        delta_mom = float(recent['delta_momentum'].iloc[-1]) if 'delta_momentum' in recent.columns else 0.0

        return {
            'buy_pressure':      round(buy_pressure, 3),
            'sell_pressure':     round(1.0 - buy_pressure, 3),
            'is_bullish':        buy_pressure > self.delta_threshold,
            'is_bearish':        buy_pressure < (1.0 - self.delta_threshold),
            'delta_momentum':    round(delta_mom, 2),
            'cumulative_delta':  round(float(recent['cumulative_delta'].iloc[-1]), 2),
        }

    def confirm_signal(self, signal_type: str, pressure: dict | None) -> bool:
        if pressure is None:
            return True
        if signal_type == 'BUY':
            if pressure['is_bullish']:
                logger.info(f"Delta confirms BUY: buy pressure {pressure['buy_pressure']*100:.1f}% | mom={pressure['delta_momentum']:+.1f}")
                return True
            logger.info(f"Delta BLOCKED BUY: buy pressure only {pressure['buy_pressure']*100:.1f}%")
            return False
        else:
            if pressure['is_bearish']:
                logger.info(f"Delta confirms SELL: sell pressure {pressure['sell_pressure']*100:.1f}% | mom={pressure['delta_momentum']:+.1f}")
                return True
            logger.info(f"Delta BLOCKED SELL: sell pressure only {pressure['sell_pressure']*100:.1f}%")
            return False

    def get_divergence(self, df: pd.DataFrame, price_col: str = 'close') -> str | None:
        """Bullish/bearish divergence: price trend opposes cumulative delta trend."""
        if df is None or len(df) < 20 or 'cumulative_delta' not in df.columns:
            return None
        recent = df.tail(20)
        price_up = float(recent[price_col].iloc[-1]) > float(recent[price_col].iloc[0])
        delta_up = float(recent['cumulative_delta'].iloc[-1]) > float(recent['cumulative_delta'].iloc[0])
        if price_up and not delta_up:
            return 'bearish_divergence'
        if not price_up and delta_up:
            return 'bullish_divergence'
        return None
