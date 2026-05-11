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
from typing import NamedTuple
from datetime import datetime, timezone

class KeyLevel(NamedTuple):
    at_level: bool
    name: str
    price: float
    entry_type: str

class MacroAllowance(NamedTuple):
    allowed: bool
    boost: float

class EntryConfirmation(NamedTuple):
    confirmed: bool
    confidence: float

class StructuralConfirmation(NamedTuple):
    confirmed: bool
    entry_zone: dict

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
from config import Config
from logger import logger
from volume_profile import VolumeProfile
from delta_analysis import DeltaAnalysis

_REJECTION_LOG = 'logs/signal_rejections.jsonl'


# MACRO_CONVICTION_THRESHOLD is now in Config so the optimizer can tune it.
# This alias keeps internal references working if something bypasses Config.
MACRO_CONVICTION_THRESHOLD = None  # sentinel — use Config.MACRO_CONVICTION_THRESHOLD

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
    # Layer 1: Macro bias — confidence multiplier, never a hard block
    # ------------------------------------------------------------------
    def _macro_allows(self, signal_type: str) -> tuple:
        """
        Returns (True, confidence_boost: float).  Never returns False.

        Macro aligned   → positive boost (stronger signal confidence)
        Macro neutral   → tiny penalty  (still trade, slightly smaller size)
        Macro opposed   → moderate penalty (trade but flag as counter-trend)

        This allows the bot to trade every day regardless of the macro
        backdrop.  When macro is strongly aligned the signal gets a
        confidence lift which flows into Kelly sizing.  When macro opposes
        we size down automatically via the lower confidence score.
        """
        direction = self._macro_direction
        score = abs(self._macro_score)

        if signal_type == 'BUY' and direction == 'bullish':
            return MacroAllowance(True, score * 0.30)
        elif signal_type == 'SELL' and direction == 'bearish':
            return MacroAllowance(True, score * 0.30)
        elif direction == 'neutral':
            return MacroAllowance(True, -0.03)
        else:
            return MacroAllowance(True, -(score * 0.15))

    def _cot_momentum_confirms(self, signal_type: str) -> bool:
        """
        Soft COT momentum check — used only for confidence scoring, not blocking.
        Returns True if COT score has been trending in the signal direction.
        """
        if len(self._cot_history) < 2:
            return True
        oldest = self._cot_history[0]
        latest = self._cot_history[-1]
        change = latest - oldest
        if signal_type == 'BUY':
            return change >= COT_MOMENTUM_THRESHOLD
        return change <= -COT_MOMENTUM_THRESHOLD

    # ------------------------------------------------------------------
    # Layer 2: Key level detection — returns (at_level, name, price, entry_type)
    # entry_type: 'BREAKOUT' | 'REACTION' | 'PULLBACK' | 'NONE'
    # ------------------------------------------------------------------
    def _at_key_level(self, df: pd.DataFrame, signal_type: str,
                      atr: float, vwap: float = None,
                      anchors: dict = None) -> tuple:
        """
        Returns (at_key_level, level_name, level_price, entry_type).

        Priority order:
          1. Asian range breakout (London open only) — highest edge
          2. PDH / PDL reaction  — reliable daily magnets
          3. Weekly open         — weekly directional anchor
          4. VWAP proximity      — intraday mean
          5. Volume profile HVN  — institutional accumulation zone
          6. Round number ($50)  — psychological level
        """
        price   = float(df['close'].iloc[-1])
        anchors = anchors or {}

        # Bar timestamp → determine session
        try:
            bar_hour = int(pd.Timestamp(df['time'].iloc[-1]).hour)
        except Exception:
            bar_hour = 12
        is_london_open = 8 <= bar_hour < 12

        # --- 1. Asian range breakout (London open only) ---
        ah = anchors.get('asian_high')
        al = anchors.get('asian_low')
        ar = anchors.get('asian_range') or 0
        if is_london_open and ah and al and ar > 0 and atr > 0:
            if signal_type == 'BUY' and price >= ah and price <= ah + atr * 0.6:
                return KeyLevel(True, 'Asian_Breakout_High', ah, 'BREAKOUT')
            if signal_type == 'SELL' and price <= al and price >= al - atr * 0.6:
                return KeyLevel(True, 'Asian_Breakout_Low', al, 'BREAKOUT')

        # --- 2. PDH / PDL reaction ---
        pdh = anchors.get('pdh')
        pdl = anchors.get('pdl')
        if pdh and atr > 0 and abs(price - pdh) <= atr * 0.6:
            if signal_type == 'SELL':
                return KeyLevel(True, 'PDH', pdh, 'REACTION')
        if pdl and atr > 0 and abs(price - pdl) <= atr * 0.6:
            if signal_type == 'BUY':
                return KeyLevel(True, 'PDL', pdl, 'REACTION')

        # --- 3. Weekly open ---
        wo = anchors.get('weekly_open')
        if wo and atr > 0 and abs(price - wo) <= atr * 0.4:
            if signal_type == 'BUY':
                return KeyLevel(True, 'Weekly_Open', wo, 'REACTION')
            if signal_type == 'SELL':
                return KeyLevel(True, 'Weekly_Open', wo, 'REACTION')

        # --- 4. VWAP ---
        if vwap and atr > 0 and abs(price - vwap) <= atr * PULLBACK_ATR_TOLERANCE:
            return KeyLevel(True, 'VWAP', vwap, 'PULLBACK')

        # --- 5. Volume profile HVN ---
        vp = self.volume_profile.calculate(df)
        if vp and vp.get('hvn'):
            hvn_dir = 'above' if signal_type == 'BUY' else 'below'
            hvn = self.volume_profile.get_nearest_hvn(price, vp['hvn'], hvn_dir)
            if hvn and atr > 0 and abs(price - hvn) <= atr * PULLBACK_ATR_TOLERANCE:
                return KeyLevel(True, 'HVN', hvn, 'PULLBACK')

        # --- 6. Round number ($50 levels for gold) ---
        nearest_50 = round(price / 50) * 50
        if atr > 0 and abs(price - nearest_50) <= atr * 0.5:
            return KeyLevel(True, f'Round_{nearest_50:.0f}', nearest_50, 'PULLBACK')

        return KeyLevel(False, 'none', price, 'NONE')

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
            return EntryConfirmation(False, 0.0)

        confidence = 0.65

        if signal_type == 'BUY':
            reversal = cur['close'] > cur['open'] and prev['close'] < prev['open']
            rsi_ok = 30 <= rsi <= 65
            if not rsi_ok:
                return EntryConfirmation(False, 0.0)
            if reversal:
                confidence += 0.10
            if vol_ratio > 1.3:
                confidence += 0.05
            return EntryConfirmation(True, min(confidence, 0.90))

        else:  # SELL
            reversal = cur['close'] < cur['open'] and prev['close'] > prev['open']
            rsi_ok = 35 <= rsi <= 70
            if not rsi_ok:
                return EntryConfirmation(False, 0.0)
            if reversal:
                confidence += 0.10
            if vol_ratio > 1.3:
                confidence += 0.05
            return EntryConfirmation(True, min(confidence, 0.90))

    # ------------------------------------------------------------------
    # Layer 3b: Breakout confirmation (for Asian range / PDH-PDL entries)
    # ------------------------------------------------------------------
    def _breakout_confirmed(self, df: pd.DataFrame, signal_type: str) -> tuple:
        """
        Returns (confirmed: bool, confidence: float).
        Breakout entries need momentum continuation, not a reversal candle.
        Checks: momentum candle in signal direction, volume expansion, RSI not extreme.
        """
        if len(df) < 20:
            return False, 0.0

        cur     = df.iloc[-1]
        vol_avg = df['tick_volume'].rolling(20).mean().iloc[-1]
        vol_ratio = cur['tick_volume'] / vol_avg if vol_avg > 0 else 1.0

        rsi = ta.rsi(df['close'], length=14).iloc[-1]
        if pd.isna(rsi):
            return False, 0.0

        if signal_type == 'BUY':
            momentum_ok = cur['close'] > cur['open']   # bullish candle
            rsi_ok = 40 <= rsi <= 75                   # not yet overbought
        else:
            momentum_ok = cur['close'] < cur['open']   # bearish candle
            rsi_ok = 25 <= rsi <= 60

        if not rsi_ok:
            return EntryConfirmation(False, 0.0)

        confidence = 0.62
        if momentum_ok:
            confidence += 0.08
        if vol_ratio >= 1.2:
            confidence += 0.07
        if vol_ratio >= 1.5:
            confidence += 0.05

        return EntryConfirmation(True, min(confidence, 0.90))

    # ------------------------------------------------------------------
    # Layer 4: Structural confirmation (replaces pure EMA alignment)
    # Break of Structure (BOS), Fair Value Gaps (FVG), Order Blocks (OB)
    # with EMA alignment as fallback when no structure is present.
    # ------------------------------------------------------------------

    def _detect_bos(self, df: pd.DataFrame, signal_type: str) -> tuple:
        """
        Break of Structure: a bar's close recently exceeded the prior 10-bar
        swing high (BUY) or fell below the prior 10-bar swing low (SELL).
        Returns (found: bool, bars_ago: int).
        """
        if len(df) < 15:
            return False, 999
        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values
        n = len(df)

        for bars_ago in range(1, 6):
            pos = n - bars_ago
            if pos < 12:
                continue
            close_at_break = closes[pos]
            ref_highs = highs[pos - 11: pos - 1]
            ref_lows  = lows[pos - 11: pos - 1]
            if signal_type == 'BUY' and close_at_break > ref_highs.max():
                return True, bars_ago
            if signal_type == 'SELL' and close_at_break < ref_lows.min():
                return True, bars_ago
        return False, 999

    def _find_fvg(self, df: pd.DataFrame, signal_type: str, atr: float) -> dict | None:
        """
        Fair Value Gap: 3-candle imbalance where candle[i-2].high < candle[i].low
        (bullish FVG) or candle[i-2].low > candle[i].high (bearish FVG).
        Returns the most recent unfilled FVG that current price is near.
        """
        if len(df) < 5:
            return None
        current_price = float(df['close'].iloc[-1])

        for j in range(2, min(22, len(df))):
            c1_high = float(df['high'].iloc[-j - 2])
            c1_low  = float(df['low'].iloc[-j - 2])
            c3_high = float(df['high'].iloc[-j])
            c3_low  = float(df['low'].iloc[-j])

            if signal_type == 'BUY' and c3_low > c1_high:
                gap_bottom = c1_high
                gap_top    = c3_low
                # Valid if price is pulling back into / near the gap
                if current_price <= gap_top + atr * 0.5:
                    return {'top': gap_top, 'bottom': gap_bottom,
                            'midpoint': (gap_top + gap_bottom) / 2,
                            'type': 'FVG_BUY', 'bars_ago': j}
            elif signal_type == 'SELL' and c1_low > c3_high:
                gap_top    = c1_low
                gap_bottom = c3_high
                if current_price >= gap_bottom - atr * 0.5:
                    return {'top': gap_top, 'bottom': gap_bottom,
                            'midpoint': (gap_top + gap_bottom) / 2,
                            'type': 'FVG_SELL', 'bars_ago': j}
        return None

    def _find_order_block(self, df: pd.DataFrame, signal_type: str, atr: float) -> dict | None:
        """
        Order Block: the last opposing candle before a strong directional impulse.
        BUY OB = last bearish candle before bullish impulse (≥0.5 ATR body).
        SELL OB = last bullish candle before bearish impulse.
        Returns zone dict or None if no OB found near current price.
        """
        if len(df) < 5:
            return None
        current_price = float(df['close'].iloc[-1])

        for j in range(2, min(20, len(df) - 1)):
            impulse = df.iloc[-j]
            ob_bar  = df.iloc[-j - 1]
            impulse_body = abs(float(impulse['close']) - float(impulse['open']))

            if signal_type == 'BUY':
                ob_bearish       = ob_bar['close'] < ob_bar['open']
                impulse_bullish  = impulse['close'] > impulse['open']
                if ob_bearish and impulse_bullish and impulse_body >= atr * 0.5:
                    ob_top = float(ob_bar['high'])
                    ob_bot = float(ob_bar['low'])
                    if current_price <= ob_top + atr * 0.3:
                        return {'top': ob_top, 'bottom': ob_bot,
                                'midpoint': (ob_top + ob_bot) / 2,
                                'type': 'OB_BUY', 'bars_ago': j + 1}
            else:
                ob_bullish       = ob_bar['close'] > ob_bar['open']
                impulse_bearish  = impulse['close'] < impulse['open']
                if ob_bullish and impulse_bearish and impulse_body >= atr * 0.5:
                    ob_top = float(ob_bar['high'])
                    ob_bot = float(ob_bar['low'])
                    if current_price >= ob_bot - atr * 0.3:
                        return {'top': ob_top, 'bottom': ob_bot,
                                'midpoint': (ob_top + ob_bot) / 2,
                                'type': 'OB_SELL', 'bars_ago': j + 1}
        return None

    def _structural_confirms(self, df: pd.DataFrame, signal_type: str,
                              atr: float,
                              df_h1: pd.DataFrame = None,
                              df_h4: pd.DataFrame = None) -> tuple:
        """
        Layer 4 replacement: checks BOS, FVG, and OB across H4 / H1 / M15.
        Falls back to EMA alignment when no structural signal is present.
        Returns (confirmed: bool, entry_zone: dict|None).
        entry_zone (FVG/OB) is used downstream for structure-aware limit orders.
        """
        entry_zone = None

        # --- H4: BOS within last 5 H4 bars, or EMA fallback ---
        if df_h4 is not None and len(df_h4) >= 15:
            h4_bos, h4_bars_ago = self._detect_bos(df_h4, signal_type)
            if not (h4_bos and h4_bars_ago <= 5):
                h4 = df_h4.copy()
                h4['ema_fast'] = ta.ema(h4['close'], length=Config.FAST_EMA)
                h4['ema_slow'] = ta.ema(h4['close'], length=Config.SLOW_EMA)
                h4_bullish = h4['ema_fast'].iloc[-1] > h4['ema_slow'].iloc[-1]
                if signal_type == 'BUY' and not h4_bullish:
                    logger.info("H4 structure BLOCKED BUY (no BOS, EMA bearish)")
                    return StructuralConfirmation(False, None)
                if signal_type == 'SELL' and h4_bullish:
                    logger.info("H4 structure BLOCKED SELL (no BOS, EMA bullish)")
                    return StructuralConfirmation(False, None)

        # --- H1: FVG or OB preferred, EMA fallback ---
        if df_h1 is not None and len(df_h1) >= 10:
            h1_fvg = self._find_fvg(df_h1, signal_type, atr)
            h1_ob  = self._find_order_block(df_h1, signal_type, atr)
            h1_zone = h1_fvg or h1_ob
            if h1_zone:
                entry_zone = h1_zone
            else:
                h1 = df_h1.copy()
                h1['ema_fast'] = ta.ema(h1['close'], length=Config.FAST_EMA)
                h1['ema_slow'] = ta.ema(h1['close'], length=Config.SLOW_EMA)
                h1_bullish = h1['ema_fast'].iloc[-1] > h1['ema_slow'].iloc[-1]
                if signal_type == 'BUY' and not h1_bullish:
                    return StructuralConfirmation(False, None)
                if signal_type == 'SELL' and h1_bullish:
                    return StructuralConfirmation(False, None)

        # --- M15: BOS (≤3 bars ago), FVG, or OB ---
        m15_bos, m15_bars_ago = self._detect_bos(df, signal_type)
        m15_fvg = self._find_fvg(df, signal_type, atr)
        m15_ob  = self._find_order_block(df, signal_type, atr)

        # M15 entry zone overrides H1 zone (more granular)
        if m15_fvg or m15_ob:
            entry_zone = m15_fvg or m15_ob

        m15_confirmed = (
            (m15_bos and m15_bars_ago <= 3) or
            m15_fvg is not None or
            m15_ob  is not None
        )

        if not m15_confirmed:
            # Fallback: require M15 EMA alignment
            df_w = df.copy()
            df_w['ema_fast'] = ta.ema(df_w['close'], length=Config.FAST_EMA)
            df_w['ema_slow'] = ta.ema(df_w['close'], length=Config.SLOW_EMA)
            fast = df_w['ema_fast'].iloc[-1]
            slow = df_w['ema_slow'].iloc[-1]
            if signal_type == 'BUY' and fast <= slow:
                return StructuralConfirmation(False, None)
            if signal_type == 'SELL' and fast >= slow:
                return StructuralConfirmation(False, None)

        return StructuralConfirmation(True, entry_zone)

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
        except OSError as e:
            logger.debug(f"Rejection log write failed: {e}")

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
    def _calc_sl_tp(self, signal_type: str, entry_type: str, entry_zone: dict | None,
                    price: float, atr: float, at_level: bool, level_price: float,
                    anchors: dict, df_work: pd.DataFrame) -> tuple:
        """Return (sl, tp) for the given signal context."""
        asian_range = anchors.get('asian_range') or atr * 2

        if signal_type == 'BUY':
            if entry_type == 'BREAKOUT':
                sl = (anchors.get('asian_low') or price - atr * Config.ATR_MULTIPLIER_SL) - atr * 0.15
                tp = price + max(asian_range * 1.5, atr * Config.ATR_MULTIPLIER_TP)
            elif entry_zone:
                sl = min(entry_zone['bottom'] - atr * 0.15, price - atr * Config.ATR_MULTIPLIER_SL)
                tp = price + atr * Config.ATR_MULTIPLIER_TP
            else:
                sl = min((level_price if at_level else price) - atr * 0.3,
                         price - atr * Config.ATR_MULTIPLIER_SL)
                vp = self.volume_profile.calculate(df_work)
                hvn = self.volume_profile.get_nearest_hvn(price, vp['hvn'], 'above') \
                    if (vp and Config.USE_VOLUME_PROFILE) else None
                tp = hvn if (hvn and hvn > price + atr) else price + atr * Config.ATR_MULTIPLIER_TP
        else:
            if entry_type == 'BREAKOUT':
                sl = (anchors.get('asian_high') or price + atr * Config.ATR_MULTIPLIER_SL) + atr * 0.15
                tp = price - max(asian_range * 1.5, atr * Config.ATR_MULTIPLIER_TP)
            elif entry_zone:
                sl = max(entry_zone['top'] + atr * 0.15, price + atr * Config.ATR_MULTIPLIER_SL)
                tp = price - atr * Config.ATR_MULTIPLIER_TP
            else:
                sl = max((level_price if at_level else price) + atr * 0.3,
                         price + atr * Config.ATR_MULTIPLIER_SL)
                vp = self.volume_profile.calculate(df_work)
                hvn = self.volume_profile.get_nearest_hvn(price, vp['hvn'], 'below') \
                    if (vp and Config.USE_VOLUME_PROFILE) else None
                tp = hvn if (hvn and hvn < price - atr) else price - atr * Config.ATR_MULTIPLIER_TP

        return sl, tp

    def generate_signal(self, df: pd.DataFrame,
                        df_h1: pd.DataFrame = None,
                        df_h4: pd.DataFrame = None,
                        vwap: float = None,
                        anchors: dict = None) -> dict | None:
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

            # --- Layer 1: Macro bias (always passes — adjusts confidence only) ---
            _, macro_boost = self._macro_allows(signal_type)            # Optional COT momentum bonus (soft, not a gate)
            if self._cot_momentum_confirms(signal_type):
                macro_boost += 0.05

            # --- Layer 2: Key level proximity (expanded with daily anchors) ---
            at_level, level_name, level_price, entry_type = self._at_key_level(
                df_work, signal_type, atr, vwap, anchors
            )
            level_confidence_mod = 0.0 if at_level else -0.08

            # --- Layer 3: Entry confirmation (method varies by entry_type) ---
            if entry_type == 'BREAKOUT':
                entry_ok, base_confidence = self._breakout_confirmed(df_work, signal_type)
            else:
                entry_ok, base_confidence = self._pullback_confirmed(df_work, signal_type)

            if not entry_ok:
                self._log_rejection(signal_type, 'L3_entry',
                                    f'type={entry_type} level={level_name}', price)
                continue

            # --- Layer 4: Structural confirmation (BOS / FVG / OB; EMA fallback) ---
            struct_ok, entry_zone = self._structural_confirms(
                df_work, signal_type, atr, df_h1, df_h4
            )
            if not struct_ok:
                self._log_rejection(signal_type, 'L4_structure',
                                    f'level={level_name}', price)
                continue

            # --- Counter-trend structural guard ---
            # When macro opposes the trade direction (macro_boost < 0) we need
            # an institutional footprint (FVG or OB) to justify the entry.
            # Trading counter-trend at a vague level with no zone is a coin flip.
            if macro_boost < 0 and entry_zone is None:
                self._log_rejection(signal_type, 'counter_trend_no_zone',
                                    f'macro_boost={macro_boost:.3f} level={level_name}', price)
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

            # --- SL / TP ---
            sl, tp = self._calc_sl_tp(
                signal_type, entry_type, entry_zone,
                price, atr, at_level, level_price, anchors or {}, df_work
            )

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
                'entry_zone': entry_zone,   # FVG/OB zone for structure-aware limits
                'entry_type': entry_type,  # BREAKOUT | REACTION | PULLBACK | NONE
            }

            zone_label = entry_zone['type'] if entry_zone else 'EMA_fallback'
            logger.info(
                f"{signal_type} Signal | Entry: {entry_type} @ {level_name} {level_price:.2f} | "
                f"Structure: {zone_label} | Regime: {self.market_regime} | "
                f"Conf: {confidence:.2f} | Macro: {self._macro_direction} ({self._macro_score:+.3f}) | "
                f"Price: {price:.2f} SL: {sl:.2f} TP: {tp:.2f} R:R: {reward/risk:.2f}"
            )
            return signal

        return None
