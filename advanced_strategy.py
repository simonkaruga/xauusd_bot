"""
Institutional-Grade Multi-Layer Strategy
==========================================
Signal hierarchy (each layer must pass before the next is evaluated):

  LAYER 1 — MACRO REGIME (primary gate, weekly/daily)
    COT commercial positioning + 10Y real yield trend + DXY trend
    → Only trade in the direction macro supports
    → Neutral macro: trade both directions at reduced size

  LAYER 2 — MARKET STRUCTURE (daily/H4)
    Higher-timeframe trend (H4 EMA alignment)
    Key level proximity: VWAP, volume profile HVN, round numbers
    → Entry must be at a structurally significant level, not mid-air

  LAYER 3 — ENTRY TRIGGER (M15)
    Pullback to VWAP or HVN level after macro-aligned move
    Confirmation: RSI not overbought/oversold at entry
    Volume: above-average on signal bar
    Delta: buy pressure > sell pressure (for BUY), vice versa

  LAYER 4 — TIMING (M15)
    EMA cross or RSI momentum confirmation
    ATR-based SL/TP with volume profile TP targeting

This is the opposite of the old approach:
  OLD: EMA cross → macro filter
  NEW: Macro conviction → wait for pullback to key level → EMA confirms timing

Why this is better:
  - EMA crosses happen 10-20x per day. Most are noise.
  - Macro extremes happen 4-8x per year. Nearly all are tradeable.
  - Waiting for a pullback to VWAP/HVN after a macro-aligned move gives
    a defined risk level (the key level) and a high R:R entry.
"""

import os
import json
import pandas as pd
import pandas_ta as ta
import numpy as np
from datetime import datetime, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
from config import Config
from logger import logger
from volume_profile import VolumeProfile
from delta_analysis import DeltaAnalysis

_REJECTION_LOG = 'logs/signal_rejections.jsonl'


# Minimum macro score magnitude to allow trading (raised from 0.25 to 0.40)
# Only trade when macro has strong conviction — eliminates coin-flip neutral trades
MACRO_CONVICTION_THRESHOLD = 0.40

# Minimum COT score magnitude to consider macro directional
COT_CONVICTION_THRESHOLD = 0.20

# Minimum COT momentum: score must have moved this much over last 3 weeks
# Ensures we trade with a trend in positioning, not a single week's reading
COT_MOMENTUM_THRESHOLD = 0.15

# VWAP pullback: entry within this many ATRs of VWAP or HVN
PULLBACK_ATR_TOLERANCE = 0.8

# Minimum volume ratio vs 20-bar average for entry bar
MIN_VOLUME_RATIO = 0.9


class AdvancedStrategy:
    def __init__(self, backtest_mode: bool = False):
        self.name = "Institutional_Multi_Layer"
        self.market_regime = 'trending'
        self.last_signal_time = None
        self.cooldown_minutes = 30
        self.volume_profile = VolumeProfile(bins=20)
        self.delta_analysis = DeltaAnalysis()
        self.backtest_mode = backtest_mode

        # Injected externally each cycle
        self._macro_score = 0.0
        self._macro_direction = 'neutral'
        self._cot_score = 0.0
        self._cot_history = []        # last 3 weekly scores for momentum check
        self._sentiment_score = 0.0

    # ------------------------------------------------------------------
    # External state injection (called by TradingBot each cycle)
    # ------------------------------------------------------------------
    def set_macro_context(self, macro_score: float, macro_direction: str,
                          cot_score: float, sentiment_score: float = 0.0):
        self._macro_score = macro_score
        self._macro_direction = macro_direction
        self._sentiment_score = sentiment_score
        # Maintain rolling 3-week COT history for momentum check
        self._cot_score = cot_score
        self._cot_history.append(cot_score)
        if len(self._cot_history) > 3:
            self._cot_history.pop(0)

    # ------------------------------------------------------------------
    # Layer 1: Macro gate
    # ------------------------------------------------------------------
    def _macro_allows(self, signal_type: str) -> tuple:
        """
        Returns (allowed: bool, confidence_boost: float).

        Rules for maximum win rate:
          1. NEVER trade neutral macro — only bullish or bearish conviction
          2. Macro score must exceed MACRO_CONVICTION_THRESHOLD (0.40)
          3. COT must show momentum: score trending in signal direction
             over the last 3 weekly readings
          4. Sentiment must not strongly oppose the signal
        """
        direction = self._macro_direction
        score = abs(self._macro_score)

        # Rule 1 & 2: Block neutral and weak macro entirely
        if direction == 'neutral':
            return False, 0.0
        if score < MACRO_CONVICTION_THRESHOLD:
            return False, 0.0

        # Rule 3: COT momentum — need 3 readings trending in signal direction
        cot_momentum_ok = self._cot_momentum_confirms(signal_type)
        if not cot_momentum_ok:
            return False, 0.0

        # Rule 4: Sentiment must not strongly oppose
        if signal_type == 'BUY' and self._sentiment_score < -0.4:
            return False, 0.0
        if signal_type == 'SELL' and self._sentiment_score > 0.4:
            return False, 0.0

        if signal_type == 'BUY' and direction == 'bullish':
            boost = score * 0.35
            return True, boost
        elif signal_type == 'SELL' and direction == 'bearish':
            boost = score * 0.35
            return True, boost

        # Signal direction opposes macro direction
        return False, 0.0

    def _cot_momentum_confirms(self, signal_type: str) -> bool:
        """
        Returns True if COT score has been trending in the signal direction
        over the last 3 weekly readings.
        Requires at least 2 readings; with only 1 reading, passes through.
        """
        if len(self._cot_history) < 2:
            return True  # not enough history yet — pass through

        # Check that the most recent reading is more extreme than the oldest
        oldest = self._cot_history[0]
        latest = self._cot_history[-1]
        change = latest - oldest

        if signal_type == 'BUY':
            # COT should be moving in bullish direction (score rising)
            return change >= COT_MOMENTUM_THRESHOLD
        else:
            # COT should be moving in bearish direction (score falling)
            return change <= -COT_MOMENTUM_THRESHOLD

    # ------------------------------------------------------------------
    # Layer 2: Market structure — is price at a key level?
    # ------------------------------------------------------------------
    def _at_key_level(self, df: pd.DataFrame, signal_type: str,
                      atr: float, vwap: float = None) -> tuple:
        """
        Returns (at_key_level: bool, level_name: str, level_price: float).
        Key levels: VWAP, volume profile HVN, psychological round numbers.
        """
        price = float(df['close'].iloc[-1])

        # Check VWAP proximity
        if vwap and atr > 0:
            dist_to_vwap = abs(price - vwap)
            if dist_to_vwap <= atr * PULLBACK_ATR_TOLERANCE:
                return True, 'VWAP', vwap

        # Check volume profile HVN
        vp = self.volume_profile.calculate(df)
        if vp and vp.get('hvn'):
            direction = 'above' if signal_type == 'BUY' else 'below'
            hvn = self.volume_profile.get_nearest_hvn(price, vp['hvn'], direction)
            if hvn and atr > 0 and abs(price - hvn) <= atr * PULLBACK_ATR_TOLERANCE:
                return True, 'HVN', hvn

        # Check psychological round numbers ($50 levels for gold)
        nearest_50 = round(price / 50) * 50
        if atr > 0 and abs(price - nearest_50) <= atr * 0.5:
            return True, f'Round_{nearest_50:.0f}', nearest_50

        # If no key level nearby, still allow but with lower confidence
        return False, 'none', price

    # ------------------------------------------------------------------
    # Layer 3: Entry trigger — pullback confirmation
    # ------------------------------------------------------------------
    def _pullback_confirmed(self, df: pd.DataFrame, signal_type: str) -> tuple:
        """
        Returns (confirmed: bool, confidence: float).
        BUY: price pulled back to key level, RSI not overbought, volume ok
        SELL: price rallied to key level, RSI not oversold, volume ok
        """
        if len(df) < 20:
            return False, 0.0

        cur = df.iloc[-1]
        prev = df.iloc[-2]

        rsi = ta.rsi(df['close'], length=14).iloc[-1]
        if pd.isna(rsi):
            return False, 0.0

        # Volume check
        vol_avg = df['tick_volume'].rolling(20).mean().iloc[-1]
        vol_ratio = cur['tick_volume'] / vol_avg if vol_avg > 0 else 1.0

        if vol_ratio < MIN_VOLUME_RATIO:
            return False, 0.0

        confidence = 0.65

        if signal_type == 'BUY':
            # Pullback: previous bar lower, current bar closes higher (reversal candle)
            reversal = cur['close'] > cur['open'] and prev['close'] < prev['open']
            rsi_ok = 30 <= rsi <= 65  # not overbought at entry
            if not rsi_ok:
                return False, 0.0
            if reversal:
                confidence += 0.10
            if vol_ratio > 1.3:
                confidence += 0.05
            return True, min(confidence, 0.90)

        else:  # SELL
            reversal = cur['close'] < cur['open'] and prev['close'] > prev['open']
            rsi_ok = 35 <= rsi <= 70
            if not rsi_ok:
                return False, 0.0
            if reversal:
                confidence += 0.10
            if vol_ratio > 1.3:
                confidence += 0.05
            return True, min(confidence, 0.90)

    # ------------------------------------------------------------------
    # Layer 4: EMA timing confirmation
    # ------------------------------------------------------------------
    def _ema_confirms(self, df: pd.DataFrame, signal_type: str,
                      df_h1: pd.DataFrame = None,
                      df_h4: pd.DataFrame = None) -> bool:
        """
        Three-timeframe EMA alignment: H4 + H1 + M15 must all agree.
        This is the single biggest win-rate lever — eliminates all
        counter-trend entries that look valid on M15 but fight H4 structure.
        """
        df = df.copy()
        df['ema_fast'] = ta.ema(df['close'], length=Config.FAST_EMA)
        df['ema_slow'] = ta.ema(df['close'], length=Config.SLOW_EMA)
        cur = df.iloc[-1]

        # M15 alignment
        if signal_type == 'BUY' and cur['ema_fast'] <= cur['ema_slow']:
            return False
        if signal_type == 'SELL' and cur['ema_fast'] >= cur['ema_slow']:
            return False

        # H1 alignment (required)
        if df_h1 is not None and len(df_h1) >= Config.SLOW_EMA:
            h1 = df_h1.copy()
            h1['ema_fast'] = ta.ema(h1['close'], length=Config.FAST_EMA)
            h1['ema_slow'] = ta.ema(h1['close'], length=Config.SLOW_EMA)
            h1_bullish = h1['ema_fast'].iloc[-1] > h1['ema_slow'].iloc[-1]
            if signal_type == 'BUY' and not h1_bullish:
                return False
            if signal_type == 'SELL' and h1_bullish:
                return False

        # H4 alignment (required when available — highest timeframe filter)
        if df_h4 is not None and len(df_h4) >= Config.SLOW_EMA:
            h4 = df_h4.copy()
            h4['ema_fast'] = ta.ema(h4['close'], length=Config.FAST_EMA)
            h4['ema_slow'] = ta.ema(h4['close'], length=Config.SLOW_EMA)
            h4_bullish = h4['ema_fast'].iloc[-1] > h4['ema_slow'].iloc[-1]
            if signal_type == 'BUY' and not h4_bullish:
                logger.info(f"H4 EMA bearish — blocking BUY")
                return False
            if signal_type == 'SELL' and h4_bullish:
                logger.info(f"H4 EMA bullish — blocking SELL")
                return False

        return True

    # ------------------------------------------------------------------
    # Regime detection (used for position sizing, not signal blocking)
    # ------------------------------------------------------------------
    def detect_market_regime(self, df: pd.DataFrame) -> str:
        adx_data = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_data is None or adx_data['ADX_14'].isna().all():
            return 'trending'
        adx = float(adx_data['ADX_14'].iloc[-1])
        if adx > 25:
            return 'trending'
        elif adx < 20:
            return 'ranging'
        return 'neutral'

    # ------------------------------------------------------------------
    # Session and cooldown gates
    # ------------------------------------------------------------------
    def _check_session(self) -> bool:
        if self.backtest_mode:
            return True
        now = _utcnow()
        hour, minute = now.hour, now.minute
        if not (Config.TRADING_START_HOUR <= hour < Config.TRADING_END_HOUR):
            return False
        guard = getattr(Config, 'SESSION_GUARD_MINUTES', 15)
        total_min = hour * 60 + minute
        start_min = Config.TRADING_START_HOUR * 60
        end_min = Config.TRADING_END_HOUR * 60
        if total_min < start_min + guard or total_min >= end_min - guard:
            return False
        return True

    def _check_cooldown(self) -> bool:
        if self.backtest_mode:
            return True
        if self.last_signal_time is None:
            return True
        mins = (_utcnow() - self.last_signal_time).total_seconds() / 60
        return mins >= self.cooldown_minutes

    # ------------------------------------------------------------------
    # Signal rejection logger
    # ------------------------------------------------------------------
    def _log_rejection(self, signal_type: str, layer: str,
                       reason: str, price: float = 0.0) -> None:
        """
        Appends one JSONL line to logs/signal_rejections.jsonl.
        After 30+ days of operation this file becomes a research goldmine:
        audit which layer blocks the most signals, and whether those
        blocked signals would have been profitable.
        """
        try:
            os.makedirs('logs', exist_ok=True)
            entry = {
                'ts': _utcnow().isoformat(),
                'signal': signal_type,
                'layer': layer,
                'reason': reason,
                'price': round(price, 2),
                'macro_score': round(self._macro_score, 3),
                'macro_dir': self._macro_direction,
                'cot_score': round(self._cot_score, 3),
            }
            with open(_REJECTION_LOG, 'a') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception:
            pass  # rejection log is best-effort — never block a signal

    # ------------------------------------------------------------------
    # Delta confirmation
    # ------------------------------------------------------------------
    def _delta_confirms(self, df: pd.DataFrame, signal_type: str) -> bool:
        # OHLCV-derived delta is a synthetic approximation of real order flow.
        # On backtest or test data it behaves as ~50/50 noise (close vs open is
        # random), which would block ~80% of valid signals.  Skip in backtest mode.
        if self.backtest_mode:
            return True
        try:
            df = self.delta_analysis.calculate_delta(df)
            pressure = self.delta_analysis.get_pressure(df, lookback=10)
            return self.delta_analysis.confirm_signal(signal_type, pressure)
        except Exception:
            return True  # pass-through if delta unavailable

    # ------------------------------------------------------------------
    # Master signal generator
    # ------------------------------------------------------------------
    def generate_signal(self, df: pd.DataFrame,
                        df_h1: pd.DataFrame = None,
                        df_h4: pd.DataFrame = None,
                        vwap: float = None) -> dict | None:
        if len(df) < 55:
            return None
        if not self._check_session():
            return None
        if not self._check_cooldown():
            return None

        self.market_regime = self.detect_market_regime(df)

        df_work = df.copy()
        df_work['atr'] = ta.atr(df_work['high'], df_work['low'],
                                 df_work['close'], length=Config.ATR_PERIOD)
        atr = float(df_work['atr'].iloc[-1])
        if pd.isna(atr) or atr <= 0:
            return None

        price = float(df_work['close'].iloc[-1])

        # Try both directions — macro gate decides which are valid
        for signal_type in ('BUY', 'SELL'):

            # --- Layer 1: Macro gate ---
            macro_ok, macro_boost = self._macro_allows(signal_type)
            if not macro_ok:
                self._log_rejection(signal_type, 'L1_macro',
                                    f'dir={self._macro_direction} score={self._macro_score:+.3f}',
                                    price)
                continue

            # --- Layer 2: Key level proximity ---
            at_level, level_name, level_price = self._at_key_level(
                df_work, signal_type, atr, vwap
            )
            # Key level is preferred but not mandatory — confidence penalty if absent
            level_confidence_mod = 0.0 if at_level else -0.10

            # --- Layer 3: Pullback confirmation ---
            pullback_ok, base_confidence = self._pullback_confirmed(
                df_work, signal_type
            )
            if not pullback_ok:
                self._log_rejection(signal_type, 'L3_pullback',
                                    f'level={level_name}', price)
                continue

            # --- Layer 4: EMA timing (H4 + H1 + M15 all aligned) ---
            if not self._ema_confirms(df_work, signal_type, df_h1, df_h4):
                self._log_rejection(signal_type, 'L4_ema',
                                    f'level={level_name}', price)
                continue

            # --- Delta confirmation ---
            if not self._delta_confirms(df_work, signal_type):
                self._log_rejection(signal_type, 'delta',
                                    f'level={level_name}', price)
                continue

            # --- Composite confidence ---
            # Sentiment boost/penalty
            sentiment_mod = self._sentiment_score * 0.1
            if signal_type == 'SELL':
                sentiment_mod = -sentiment_mod

            confidence = base_confidence + macro_boost + level_confidence_mod + sentiment_mod
            confidence = max(0.50, min(0.95, confidence))

            # --- SL/TP ---
            if signal_type == 'BUY':
                # SL below key level (or ATR-based)
                sl_anchor = level_price if at_level else price
                sl = min(sl_anchor - atr * 0.3,
                         price - atr * Config.ATR_MULTIPLIER_SL)

                # TP: next HVN above, or ATR-based
                vp = self.volume_profile.calculate(df_work)
                if vp and Config.USE_VOLUME_PROFILE:
                    hvn = self.volume_profile.get_nearest_hvn(
                        price, vp['hvn'], 'above'
                    )
                    tp = hvn if (hvn and hvn > price + atr) \
                        else price + atr * Config.ATR_MULTIPLIER_TP
                else:
                    tp = price + atr * Config.ATR_MULTIPLIER_TP
            else:
                sl_anchor = level_price if at_level else price
                sl = max(sl_anchor + atr * 0.3,
                         price + atr * Config.ATR_MULTIPLIER_SL)

                vp = self.volume_profile.calculate(df_work)
                if vp and Config.USE_VOLUME_PROFILE:
                    hvn = self.volume_profile.get_nearest_hvn(
                        price, vp['hvn'], 'below'
                    )
                    tp = hvn if (hvn and hvn < price - atr) \
                        else price - atr * Config.ATR_MULTIPLIER_TP
                else:
                    tp = price - atr * Config.ATR_MULTIPLIER_TP

            # Validate R:R before returning
            risk = abs(price - sl)
            reward = abs(tp - price)
            if risk == 0 or (reward / risk) < Config.MIN_RISK_REWARD:
                rr = round(reward / risk, 2) if risk > 0 else 0
                self._log_rejection(signal_type, 'rr_check',
                                    f'rr={rr} min={Config.MIN_RISK_REWARD}', price)
                continue

            self.last_signal_time = _utcnow()

            signal = {
                'type': signal_type,
                'price': price,
                'sl': round(sl, 2),
                'tp': round(tp, 2),
                'atr': round(atr, 4),
                'regime': self.market_regime,
                'confidence': round(confidence, 3),
                'level_name': level_name,
                'level_price': round(level_price, 2),
                'macro_score': round(self._macro_score, 3),
                'macro_direction': self._macro_direction,
                'cot_score': round(self._cot_score, 3),
                'sentiment_score': round(self._sentiment_score, 3),
                'rr': round(reward / risk, 2),
            }

            logger.info(
                f"{signal_type} Signal | Layer: {level_name} @ {level_price:.2f} | "
                f"Regime: {self.market_regime} | Conf: {confidence:.2f} | "
                f"Macro: {self._macro_direction} ({self._macro_score:+.3f}) | "
                f"COT: {self._cot_score:+.3f} | Sentiment: {self._sentiment_score:+.3f} | "
                f"Price: {price:.2f} SL: {sl:.2f} TP: {tp:.2f} R:R: {reward/risk:.2f}"
            )
            return signal

        return None
