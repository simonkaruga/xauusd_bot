"""
Macro Engine
=============
Gold's fundamental drivers (four-component composite):

1. US 10-Year Real Yield (TIPS) — fetched from FRED (DFII10).
   Real yield rising  → gold falls  (opportunity cost rises)
   Real yield falling → gold rises  (gold becomes relatively attractive)
   Weight: ±0.40

2. DXY trend — proxied via EURUSD from MT5.
   DXY rising  → gold headwind
   DXY falling → gold tailwind
   Weight: ±0.30 × strength

3. COT commercial positioning — injected via set_cot_score().
   Weight: ±0.30 (cot_score -1 to +1)

4. US Yield Curve (10Y–2Y spread, FRED T10Y2Y).
   Inverted curve (negative spread) → recession risk → bullish gold
   Steepening curve → growth/risk-on → bearish gold
   Weight: ±0.15

Combined macro score: -1.0 (strong bearish for gold) to +1.0 (strong bullish).

Threshold logic:
  score >= +0.3  → macro bullish  → only take BUY signals
  score <= -0.3  → macro bearish  → only take SELL signals
  -0.3 < score < +0.3 → neutral  → take both, reduced size

Cache: 4 hours (FRED updates daily, no need to hammer the API)
"""

import os
import json
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
from logger import logger

_CACHE_PATH = 'logs/macro_cache.json'
_CACHE_TTL_HOURS = 4

# FRED series IDs (all public, no API key required for basic access)
# DFII10  = 10-Year TIPS real yield
# T10Y2Y  = 10-Year minus 2-Year Treasury spread (yield curve)
_FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="


class MacroEngine:
    def __init__(self, connector=None):
        self.connector = connector
        self._cot_score = 0.0
        self._cache = self._load_cache()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_macro_score(self) -> dict:
        """
        Returns:
          score             : -1.0 to +1.0 composite macro bias for gold
          direction         : 'bullish' | 'bearish' | 'neutral'
          real_yield        : latest 10Y TIPS real yield (%)
          yield_trend       : 'rising' | 'falling' | 'flat'
          dxy_trend         : 'rising' | 'falling' | 'flat'
          yield_curve_spread: 10Y-2Y spread in % (negative = inverted)
          cot_score         : -1 to +1 commercial positioning
          allow_buy         : bool
          allow_sell        : bool
        """
        if self._is_cache_fresh():
            cached = self._cache.get('result')
            if cached:
                logger.info(
                    f"Macro (cached): score={cached['score']:+.3f} "
                    f"direction={cached['direction']}"
                )
                return cached

        real_yield, yield_trend = self._get_real_yield()
        dxy_trend, dxy_strength = self._get_dxy_trend()
        curve_spread, curve_score = self._get_yield_curve()

        # --- Score components ---
        # Real yield: falling = bullish for gold (+), rising = bearish (-)
        if yield_trend == 'falling':
            yield_score = 0.4
        elif yield_trend == 'rising':
            yield_score = -0.4
        else:
            yield_score = 0.0

        # DXY: falling = bullish for gold (+), rising = bearish (-)
        if dxy_trend == 'falling':
            dxy_score = 0.3 * dxy_strength
        elif dxy_trend == 'rising':
            dxy_score = -0.3 * dxy_strength
        else:
            dxy_score = 0.0

        # COT: already -1 to +1, weight at 0.3
        cot_score = 0.3 * self._cot_score

        # Yield curve: inverted (negative) = recession risk = bullish gold
        # curve_score already scaled ±0.15 by _get_yield_curve()

        # Composite
        raw_score = yield_score + dxy_score + cot_score + curve_score
        score = max(-1.0, min(1.0, raw_score))

        if score >= 0.25:
            direction = 'bullish'
        elif score <= -0.25:
            direction = 'bearish'
        else:
            direction = 'neutral'

        result = {
            'score': round(score, 3),
            'direction': direction,
            'real_yield': real_yield,
            'yield_trend': yield_trend,
            'dxy_trend': dxy_trend,
            'dxy_strength': round(dxy_strength, 3),
            'yield_curve_spread': round(curve_spread, 3),
            'yield_curve_score': round(curve_score, 3),
            'cot_score': round(self._cot_score, 3),
            'allow_buy': direction in ('bullish', 'neutral'),
            'allow_sell': direction in ('bearish', 'neutral'),
            'timestamp': _utcnow().isoformat(),
        }

        self._save_cache(result)
        logger.info(
            f"Macro Engine: score={score:+.3f} direction={direction} | "
            f"yield={real_yield:.2f}% ({yield_trend}) | "
            f"DXY={dxy_trend} ({dxy_strength:.2f}) | "
            f"curve={curve_spread:+.2f}% (score={curve_score:+.3f}) | "
            f"COT={self._cot_score:+.3f}"
        )
        return result

    def set_cot_score(self, score: float):
        self._cot_score = float(score)

    def allows_signal(self, signal_type: str) -> tuple:
        """
        Returns (allowed: bool, size_multiplier: float, reason: str).
        Hard-blocks signals that go against strong macro conviction.
        """
        macro = self.get_macro_score()
        score = macro['score']
        direction = macro['direction']

        if signal_type == 'BUY':
            if direction == 'bearish':
                return False, 0.0, f"Macro BEARISH (score={score:+.3f}) — blocking BUY"
            elif direction == 'bullish':
                mult = min(1.3, 1.0 + abs(score) * 0.5)
                return True, mult, f"Macro BULLISH (score={score:+.3f}) — boosting BUY x{mult:.2f}"
            else:
                return True, 0.85, f"Macro NEUTRAL (score={score:+.3f}) — reduced BUY size"

        else:  # SELL
            if direction == 'bullish':
                return False, 0.0, f"Macro BULLISH (score={score:+.3f}) — blocking SELL"
            elif direction == 'bearish':
                mult = min(1.3, 1.0 + abs(score) * 0.5)
                return True, mult, f"Macro BEARISH (score={score:+.3f}) — boosting SELL x{mult:.2f}"
            else:
                return True, 0.85, f"Macro NEUTRAL (score={score:+.3f}) — reduced SELL size"

    # ------------------------------------------------------------------
    # 10Y TIPS Real Yield (FRED)
    # ------------------------------------------------------------------
    def _get_real_yield(self) -> tuple:
        """Returns (latest_yield_pct, trend: 'rising'|'falling'|'flat')."""
        try:
            url = _FRED_BASE + "DFII10"
            resp = requests.get(url, timeout=15,
                                headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code != 200:
                raise ValueError(f"FRED HTTP {resp.status_code}")

            lines = resp.text.strip().split('\n')
            # Format: DATE,DFII10
            rows = [l.split(',') for l in lines[1:] if len(l.split(',')) == 2]
            # Filter out missing values ('.')
            rows = [(d, float(v)) for d, v in rows if v.strip() not in ('.', '')]
            if len(rows) < 10:
                raise ValueError("Insufficient FRED data")

            latest = rows[-1][1]
            # 20-day trend: compare latest vs 20 days ago
            prev = rows[-20][1] if len(rows) >= 20 else rows[0][1]
            change = latest - prev

            if change > 0.05:
                trend = 'rising'
            elif change < -0.05:
                trend = 'falling'
            else:
                trend = 'flat'

            logger.info(f"TIPS real yield: {latest:.2f}% (20d change: {change:+.2f}%) → {trend}")
            return latest, trend

        except Exception as e:
            logger.warning(f"FRED real yield fetch failed: {e} — using neutral")
            return 0.0, 'flat'

    # ------------------------------------------------------------------
    # Yield curve: 10Y minus 2Y Treasury spread (FRED T10Y2Y)
    # ------------------------------------------------------------------
    def _get_yield_curve(self) -> tuple:
        """
        Returns (spread_pct, curve_score).

        spread_pct  : 10Y-2Y yield difference (negative = inverted curve)
        curve_score : ±0.15 contribution to macro composite
          <= -0.5 → deep inversion → recession risk → bullish gold (+0.15)
          <  0    → mild inversion → mildly bullish  (+0.08)
          >= 0.5  → steepening    → growth signal    (-0.08)
          >= 1.0  → steep curve   → risk-on          (-0.15)
        """
        try:
            url = _FRED_BASE + "T10Y2Y"
            resp = requests.get(url, timeout=15,
                                headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code != 200:
                raise ValueError(f"FRED HTTP {resp.status_code}")

            lines = resp.text.strip().split('\n')
            rows = [l.split(',') for l in lines[1:] if len(l.split(',')) == 2]
            rows = [(d, float(v)) for d, v in rows if v.strip() not in ('.', '')]
            if not rows:
                raise ValueError("No T10Y2Y data")

            spread = rows[-1][1]

            if spread <= -0.5:
                score = +0.15
            elif spread < 0:
                score = +0.08
            elif spread >= 1.0:
                score = -0.15
            elif spread >= 0.5:
                score = -0.08
            else:
                score = 0.0

            logger.info(f"Yield curve (10Y-2Y): {spread:+.2f}% → score={score:+.3f}")
            return spread, score

        except Exception as e:
            logger.warning(f"Yield curve fetch failed: {e} — using neutral")
            return 0.0, 0.0

    # ------------------------------------------------------------------
    # DXY trend via EURUSD from MT5
    # ------------------------------------------------------------------
    def _get_dxy_trend(self) -> tuple:
        """Returns (trend: 'rising'|'falling'|'flat', strength: 0-1)."""
        if self.connector is None:
            return 'flat', 0.0
        try:
            df = self.connector.get_bars('EURUSD', 'H4', 60)
            if df is None or len(df) < 20:
                df = self.connector.get_bars('USDCHF', 'H4', 60)
                if df is not None:
                    df = df.copy()
                    df['close'] = 1 / df['close']

            if df is None or len(df) < 20:
                return 'flat', 0.0

            close = df['close']
            ema20 = ta.ema(close, length=20).iloc[-1]
            ema50 = ta.ema(close, length=min(50, len(close) - 1)).iloc[-1]
            momentum = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20]

            # EURUSD rising = DXY falling = gold bullish
            # EURUSD falling = DXY rising = gold bearish
            strength = min(1.0, abs(momentum) / 0.005)

            if ema20 > ema50 and momentum > 0:
                # EURUSD up = DXY down = gold tailwind → report as 'falling' DXY
                return 'falling', strength
            elif ema20 < ema50 and momentum < 0:
                return 'rising', strength
            else:
                return 'flat', strength * 0.5

        except Exception as e:
            logger.warning(f"DXY trend error: {e}")
            return 'flat', 0.0

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------
    def _is_cache_fresh(self) -> bool:
        ts = self._cache.get('timestamp')
        if not ts:
            return False
        age = (_utcnow() - datetime.fromisoformat(ts)).total_seconds() / 3600
        return age < _CACHE_TTL_HOURS

    def _save_cache(self, result: dict):
        os.makedirs('logs', exist_ok=True)
        self._cache = {'timestamp': _utcnow().isoformat(), 'result': result}
        try:
            with open(_CACHE_PATH, 'w') as f:
                json.dump(self._cache, f)
        except Exception as e:
            logger.warning(f"Macro cache save failed: {e}")

    def _load_cache(self) -> dict:
        if os.path.exists(_CACHE_PATH):
            try:
                with open(_CACHE_PATH) as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
            pass
        return {}
