"""
Macro Correlation Filter for XAU/USD
======================================
Gold prices are driven by:
  1. USD strength (DXY) — inverse correlation: DXY↑ → Gold↓
  2. Real interest rates (TIPS yields) — inverse: rates↑ → Gold↓
  3. Risk appetite (equity/VIX proxy) — moderate positive with risk-off

This filter:
  - Reads USD proxy from MT5 (EURUSD or USDCHF)
  - Calculates rolling correlation coefficient
  - Blocks signals that go against macro flow
  - Provides momentum score for position sizing weight
"""

import numpy as np
import pandas as pd
import pandas_ta as ta
from logger import logger


class CorrelationFilter:
    def __init__(self, connector=None):
        self.connector = connector
        self.lookback = 20          # bars for rolling correlation
        self.strong_trend_threshold = 0.003   # 0.3% move = strong USD move
        self.correlation_block_threshold = -0.65  # block if correlation < -0.65

    # ------------------------------------------------------------------
    # Main check: should we allow a BUY/SELL on gold given macro?
    # ------------------------------------------------------------------
    def is_macro_aligned(self, signal_type: str, df_gold: pd.DataFrame,
                          df_usd_proxy: pd.DataFrame = None) -> tuple:
        """
        Returns (allowed: bool, confidence_multiplier: float, reason: str)

        confidence_multiplier: 0.5-1.2 scale applied to position size
        """
        if df_usd_proxy is None or len(df_usd_proxy) < self.lookback:
            return True, 1.0, "No USD data - pass-through"

        result = self._analyse_usd_momentum(df_usd_proxy)
        gold_momentum = self._analyse_gold_momentum(df_gold)

        usd_direction = result['direction']       # 'rising', 'falling', 'neutral'
        usd_strength = result['strength']          # 0.0-1.0
        corr = result['correlation']               # rolling corr XAUUSD vs EURUSD

        # Logic:
        # EURUSD and gold are both positively correlated (both inverse to USD)
        # If EURUSD falling (USD rising) → gold tends to fall → block BUY
        # If EURUSD rising (USD falling) → gold tends to rise → block SELL

        if signal_type == 'BUY':
            if usd_direction == 'rising' and usd_strength > 0.6:
                return False, 0.5, f"USD rising strongly ({usd_strength:.2f}) — gold headwind"
            elif usd_direction == 'rising':
                multiplier = max(0.7, 1.0 - usd_strength * 0.4)
                return True, multiplier, f"USD mildly rising — reduced size ({multiplier:.2f}x)"
            elif usd_direction == 'falling':
                multiplier = min(1.2, 1.0 + usd_strength * 0.3)
                return True, multiplier, f"USD falling — macro tailwind ({multiplier:.2f}x)"

        elif signal_type == 'SELL':
            if usd_direction == 'falling' and usd_strength > 0.6:
                return False, 0.5, f"USD falling strongly ({usd_strength:.2f}) — gold upward pressure"
            elif usd_direction == 'falling':
                multiplier = max(0.7, 1.0 - usd_strength * 0.4)
                return True, multiplier, f"USD mildly falling — reduced size ({multiplier:.2f}x)"
            elif usd_direction == 'rising':
                multiplier = min(1.2, 1.0 + usd_strength * 0.3)
                return True, multiplier, f"USD rising — macro tailwind for short ({multiplier:.2f}x)"

        return True, 1.0, "Neutral macro environment"

    # ------------------------------------------------------------------
    # USD proxy momentum analysis (EURUSD as proxy — inverse USD)
    # ------------------------------------------------------------------
    def _analyse_usd_momentum(self, df: pd.DataFrame) -> dict:
        df = df.copy()

        if len(df) < self.lookback + 5:
            return {'direction': 'neutral', 'strength': 0.0, 'correlation': 0.0}

        close = df['close']

        # Short-term trend: EMA5 vs EMA20
        ema5 = ta.ema(close, length=5)
        ema20 = ta.ema(close, length=20)
        ema_diff = (ema5.iloc[-1] - ema20.iloc[-1]) / ema20.iloc[-1]

        # Momentum: 10-bar return
        momentum = (close.iloc[-1] - close.iloc[-10]) / close.iloc[-10]

        # Strength: normalise momentum to 0-1
        strength = min(1.0, abs(momentum) / self.strong_trend_threshold)

        # EURUSD direction
        # EURUSD rising = USD weakening = gold positive
        # EURUSD falling = USD strengthening = gold negative
        if momentum > 0 and ema_diff > 0:
            direction = 'falling'   # EURUSD rising = USD falling (use 'falling' = USD falling)
        elif momentum < 0 and ema_diff < 0:
            direction = 'rising'    # EURUSD falling = USD rising
        else:
            direction = 'neutral'

        # Rolling correlation (gold vs EURUSD)
        correlation = 0.0
        if 'gold_close' in df.columns:
            corr_series = df['close'].rolling(self.lookback).corr(df['gold_close'])
            correlation = float(corr_series.iloc[-1]) if not pd.isna(corr_series.iloc[-1]) else 0.0

        return {
            'direction': direction,
            'strength': round(strength, 3),
            'momentum': round(float(momentum), 5),
            'ema_diff': round(float(ema_diff), 5),
            'correlation': round(correlation, 3),
        }

    def _analyse_gold_momentum(self, df: pd.DataFrame) -> dict:
        if len(df) < 20:
            return {'momentum': 0.0, 'trend': 'neutral'}

        close = df['close']
        momentum = (close.iloc[-1] - close.iloc[-10]) / close.iloc[-10]
        ema9 = ta.ema(close, length=9).iloc[-1]
        ema21 = ta.ema(close, length=21).iloc[-1]

        trend = 'bullish' if ema9 > ema21 else ('bearish' if ema9 < ema21 else 'neutral')

        return {
            'momentum': round(float(momentum), 5),
            'trend': trend,
            'ema9': round(float(ema9), 2),
            'ema21': round(float(ema21), 2),
        }

    # ------------------------------------------------------------------
    # Fetch USD proxy data from MT5
    # ------------------------------------------------------------------
    def get_usd_proxy_data(self, timeframe: str = 'M15', count: int = 50) -> pd.DataFrame:
        if self.connector is None:
            return None

        # EURUSD is best proxy: inverse USD (most liquid, tightest spread)
        df = self.connector.get_bars('EURUSD', timeframe, count)
        if df is None or len(df) < 20:
            # Fallback to USDCHF (direct USD proxy)
            df = self.connector.get_bars('USDCHF', timeframe, count)
            if df is not None:
                # Invert so 'rising' means USD rising like EURUSD logic
                df = df.copy()
                df['close'] = 1 / df['close']
                df['open'] = 1 / df['open']

        return df

    # ------------------------------------------------------------------
    # Quick check for live trading (returns bool + log)
    # ------------------------------------------------------------------
    def check(self, signal_type: str, df_gold: pd.DataFrame) -> tuple:
        df_usd = self.get_usd_proxy_data()
        allowed, multiplier, reason = self.is_macro_aligned(signal_type, df_gold, df_usd)

        if not allowed:
            logger.info(f"Correlation Filter BLOCKED {signal_type}: {reason}")
        else:
            logger.info(f"Correlation Filter PASSED {signal_type}: {reason} (size x{multiplier:.2f})")

        return allowed, multiplier


if __name__ == "__main__":
    # Test with synthetic data
    rng = np.random.default_rng(42)
    n = 100

    gold_df = pd.DataFrame({
        'time': pd.date_range('2024-01-01', periods=n, freq='15min'),
        'open': 2000 + np.cumsum(rng.normal(0, 2, n)),
        'high': 2000 + np.cumsum(rng.normal(0, 2, n)) + 2,
        'low': 2000 + np.cumsum(rng.normal(0, 2, n)) - 2,
        'close': 2000 + np.cumsum(rng.normal(0, 2, n)),
        'tick_volume': rng.integers(100, 1000, n),
    })
    gold_df['close'] = gold_df['close'].abs() + 1800

    eurusd_df = pd.DataFrame({
        'time': pd.date_range('2024-01-01', periods=n, freq='15min'),
        'open': 1.08 + np.cumsum(rng.normal(0, 0.001, n)),
        'high': 1.08 + np.cumsum(rng.normal(0, 0.001, n)) + 0.001,
        'low': 1.08 + np.cumsum(rng.normal(0, 0.001, n)) - 0.001,
        'close': 1.08 + np.cumsum(rng.normal(0, 0.001, n)),
        'tick_volume': rng.integers(100, 1000, n),
    })

    cf = CorrelationFilter()
    allowed, mult, reason = cf.is_macro_aligned('BUY', gold_df, eurusd_df)
    print(f"BUY allowed: {allowed}, multiplier: {mult:.2f}, reason: {reason}")
