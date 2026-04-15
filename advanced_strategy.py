"""
Advanced Multi-Strategy System
================================
Selects the right strategy based on market regime:
  - TRENDING  (ADX > 25) → EMA crossover + RSI
  - RANGING   (ADX < 20) → Bollinger Band mean reversion
  - NEUTRAL   (ADX 20-25) → Breakout above/below BB + volume confirmation

All strategies return a unified signal dict with:
  price, sl, tp, atr, regime, confidence, type
"""

import pandas as pd
import pandas_ta as ta
import numpy as np
from datetime import datetime
from config import Config
from logger import logger
from volume_profile import VolumeProfile
from delta_analysis import DeltaAnalysis


class AdvancedStrategy:
    def __init__(self, backtest_mode: bool = False):
        self.name = "Multi_Strategy_System"
        self.market_regime = 'trending'
        self.last_signal_time = None
        self.cooldown_minutes = 30
        self.volume_profile = VolumeProfile(bins=20)
        self.delta_analysis = DeltaAnalysis()
        # In backtest mode, skip real-clock session/cooldown checks.
        # The backtester iterates bars in historical order and handles its
        # own time filtering — using datetime.utcnow() here would block all
        # bars that don't coincide with the current real-world trading window.
        self.backtest_mode = backtest_mode

    # ------------------------------------------------------------------
    # Regime detection
    # ------------------------------------------------------------------
    def detect_market_regime(self, df: pd.DataFrame) -> str:
        adx_data = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_data is None or adx_data['ADX_14'].isna().all():
            return 'trending'
        current_adx = float(adx_data['ADX_14'].iloc[-1])
        if current_adx > 25:
            return 'trending'
        elif current_adx < 20:
            return 'ranging'
        return 'neutral'

    # ------------------------------------------------------------------
    # Strategy 1: EMA/RSI trend (high ADX)
    # ------------------------------------------------------------------
    def trend_strategy(self, df: pd.DataFrame) -> dict | None:
        df['ema_fast'] = ta.ema(df['close'], length=Config.FAST_EMA)
        df['ema_slow'] = ta.ema(df['close'], length=Config.SLOW_EMA)
        df['rsi'] = ta.rsi(df['close'], length=Config.RSI_PERIOD)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=Config.ATR_PERIOD)

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        # Delta confirmation
        df = self.delta_analysis.calculate_delta(df)
        pressure = self.delta_analysis.get_pressure(df, lookback=10)

        if (prev['ema_fast'] <= prev['ema_slow'] and
                cur['ema_fast'] > cur['ema_slow'] and
                Config.RSI_BUY_MIN <= cur['rsi'] <= Config.RSI_BUY_MAX and
                self.delta_analysis.confirm_signal('BUY', pressure)):
            return {'type': 'BUY', 'confidence': 0.75, 'atr': cur['atr'],
                    'price': cur['close'], 'rsi': cur['rsi']}

        if (prev['ema_fast'] >= prev['ema_slow'] and
                cur['ema_fast'] < cur['ema_slow'] and
                Config.RSI_SELL_MIN <= cur['rsi'] <= Config.RSI_SELL_MAX and
                self.delta_analysis.confirm_signal('SELL', pressure)):
            return {'type': 'SELL', 'confidence': 0.75, 'atr': cur['atr'],
                    'price': cur['close'], 'rsi': cur['rsi']}

        return None

    # ------------------------------------------------------------------
    # Strategy 2: Bollinger Band mean reversion (low ADX / ranging)
    # ------------------------------------------------------------------
    def reversal_strategy(self, df: pd.DataFrame) -> dict | None:
        df['rsi'] = ta.rsi(df['close'], length=Config.RSI_PERIOD)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=Config.ATR_PERIOD)
        bbands = ta.bbands(df['close'], length=20)
        if bbands is None:
            return None

        df['bb_upper'] = bbands['BBU_20_2.0']
        df['bb_lower'] = bbands['BBL_20_2.0']
        df['bb_mid'] = bbands['BBM_20_2.0']

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        # Oversold: price touches/breaks lower band AND RSI oversold, then bounces
        oversold = (cur['rsi'] < 35 and
                    prev['close'] <= prev['bb_lower'] and
                    cur['close'] > cur['bb_lower'])

        # Overbought: price touches/breaks upper band AND RSI overbought, then drops
        overbought = (cur['rsi'] > 65 and
                      prev['close'] >= prev['bb_upper'] and
                      cur['close'] < cur['bb_upper'])

        if oversold:
            return {'type': 'BUY', 'confidence': 0.65, 'atr': cur['atr'],
                    'price': cur['close'], 'target': cur['bb_mid'], 'rsi': cur['rsi']}
        if overbought:
            return {'type': 'SELL', 'confidence': 0.65, 'atr': cur['atr'],
                    'price': cur['close'], 'target': cur['bb_mid'], 'rsi': cur['rsi']}

        return None

    # ------------------------------------------------------------------
    # Strategy 3: Breakout (neutral/transitional market)
    # ------------------------------------------------------------------
    def breakout_strategy(self, df: pd.DataFrame) -> dict | None:
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=Config.ATR_PERIOD)
        bbands = ta.bbands(df['close'], length=20)
        if bbands is None:
            return None

        df['bb_upper'] = bbands['BBU_20_2.0']
        df['bb_lower'] = bbands['BBL_20_2.0']

        # Volume confirmation: current bar volume > 1.3x 20-bar average
        if 'tick_volume' in df.columns:
            vol_avg = df['tick_volume'].rolling(20).mean()
            vol_ok = df['tick_volume'].iloc[-1] > vol_avg.iloc[-1] * 1.3
        else:
            vol_ok = True

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        # Clean breakout: close above/below band (not just touch) with volume
        if prev['close'] <= prev['bb_upper'] and cur['close'] > cur['bb_upper'] and vol_ok:
            return {'type': 'BUY', 'confidence': 0.80, 'atr': cur['atr'],
                    'price': cur['close'], 'rsi': None}

        if prev['close'] >= prev['bb_lower'] and cur['close'] < cur['bb_lower'] and vol_ok:
            return {'type': 'SELL', 'confidence': 0.80, 'atr': cur['atr'],
                    'price': cur['close'], 'rsi': None}

        return None

    # ------------------------------------------------------------------
    # Cooldown gate
    # ------------------------------------------------------------------
    def _check_cooldown(self) -> bool:
        if self.backtest_mode:
            return True   # backtester handles bar spacing, not real-world clock
        if self.last_signal_time is None:
            return True
        mins = (datetime.now() - self.last_signal_time).total_seconds() / 60
        return mins >= self.cooldown_minutes

    def _check_session(self) -> bool:
        if self.backtest_mode:
            return True   # backtester iterates all bars; session guard would
                          # silently drop everything outside current clock window
        now = datetime.utcnow()
        hour = now.hour
        minute = now.minute

        if not (Config.TRADING_START_HOUR <= hour < Config.TRADING_END_HOUR):
            return False

        guard = getattr(Config, 'SESSION_GUARD_MINUTES', 15)
        total_minutes = hour * 60 + minute
        session_start_min = Config.TRADING_START_HOUR * 60
        session_end_min = Config.TRADING_END_HOUR * 60

        # Block first N minutes of session (spreads wide, algos front-running)
        if total_minutes < session_start_min + guard:
            logger.info(f"Session guard: within {guard}min of open — skipping")
            return False

        # Block last N minutes (liquidity drains, spreads widen)
        if total_minutes >= session_end_min - guard:
            logger.info(f"Session guard: within {guard}min of close — skipping")
            return False

        return True

    # ------------------------------------------------------------------
    # Master signal generator
    # ------------------------------------------------------------------
    def generate_signal(self, df: pd.DataFrame, df_h1: pd.DataFrame = None) -> dict | None:
        if len(df) < 55:
            return None
        if not self._check_session():
            return None
        if not self._check_cooldown():
            return None

        # Regime detection
        self.market_regime = self.detect_market_regime(df)

        # Pick strategy
        if self.market_regime == 'trending':
            signal = self.trend_strategy(df.copy())
        elif self.market_regime == 'ranging':
            signal = self.reversal_strategy(df.copy())
        else:
            # Neutral: try breakout first, then trend
            signal = self.breakout_strategy(df.copy())
            if signal is None:
                signal = self.trend_strategy(df.copy())

        if signal is None:
            return None

        # Higher-timeframe confirmation (only block, never force)
        if df_h1 is not None and len(df_h1) >= Config.SLOW_EMA:
            h1 = df_h1.copy()
            h1['ema_fast'] = ta.ema(h1['close'], length=Config.FAST_EMA)
            h1['ema_slow'] = ta.ema(h1['close'], length=Config.SLOW_EMA)
            h1_bullish = h1['ema_fast'].iloc[-1] > h1['ema_slow'].iloc[-1]

            if signal['type'] == 'BUY' and not h1_bullish:
                logger.info("Signal rejected — H1 trend bearish")
                return None
            if signal['type'] == 'SELL' and h1_bullish:
                logger.info("Signal rejected — H1 trend bullish")
                return None

        # SL/TP calculation
        atr = signal['atr']
        price = signal['price']

        if signal['type'] == 'BUY':
            sl = price - atr * Config.ATR_MULTIPLIER_SL
            # Volume-profile TP
            vp = self.volume_profile.calculate(df)
            if vp and Config.USE_VOLUME_PROFILE:
                hvn = self.volume_profile.get_nearest_hvn(price, vp['hvn'], 'above')
                tp = hvn if (hvn and hvn > price) else price + atr * Config.ATR_MULTIPLIER_TP
            else:
                tp = price + atr * Config.ATR_MULTIPLIER_TP
        else:
            sl = price + atr * Config.ATR_MULTIPLIER_SL
            vp = self.volume_profile.calculate(df)
            if vp and Config.USE_VOLUME_PROFILE:
                hvn = self.volume_profile.get_nearest_hvn(price, vp['hvn'], 'below')
                tp = hvn if (hvn and hvn < price) else price - atr * Config.ATR_MULTIPLIER_TP
            else:
                tp = price - atr * Config.ATR_MULTIPLIER_TP

        self.last_signal_time = datetime.now()

        signal.update({
            'price': price,
            'sl': sl,
            'tp': tp,
            'atr': atr,
            'regime': self.market_regime,
            'vp': vp if 'vp' in dir() else None,
        })

        logger.info(
            f"{signal['type']} Signal | Regime:{self.market_regime} "
            f"| Conf:{signal['confidence']:.2f} | Price:{price:.2f} "
            f"| SL:{sl:.2f} | TP:{tp:.2f}"
        )
        return signal
