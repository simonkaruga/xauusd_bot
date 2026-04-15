"""
Daily VWAP Bias Filter
=======================
VWAP (Volume Weighted Average Price) is the single most-watched intraday
reference level by institutional desks trading XAU/USD.

Why it matters:
  - Buy orders above VWAP are statistically more likely to succeed because
    institutions accumulating longs are defending price above VWAP.
  - Sell orders below VWAP have the same institutional backing on the short side.
  - Fading the VWAP bias (buying below, selling above) fights the dominant
    institutional flow for that session.

What this module computes:
  - Daily VWAP from M15 bars since midnight UTC of the current day
  - Standard-deviation bands (±1σ, ±2σ) around VWAP (VWAP-SD)
  - Bias score: +1 (strong above), -1 (strong below), 0 (near midpoint)
  - Alignment check: does a given signal direction agree with VWAP bias?

Integration in signal pipeline:
  vwap = VWAPFilter()
  allowed, mult = vwap.check_signal('BUY', df_m15)
  # mult < 1 if trading against VWAP, mult > 1 if confirmed by VWAP
"""

import numpy as np
import pandas as pd
from logger import logger


class VWAPFilter:
    # If price is within this fraction of VWAP, considered neutral (no bias)
    NEUTRAL_BAND_PCT = 0.05     # 0.05% of price ≈ $1 on $2000 gold

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Compute daily VWAP + standard deviation bands from M15 bars
    # ------------------------------------------------------------------
    def compute(self, df: pd.DataFrame) -> dict:
        """
        df: M15 OHLC dataframe with 'time', 'high', 'low', 'close', 'tick_volume'.
        Returns VWAP and SD bands for the current day's session.
        """
        df = df.copy()
        df['time'] = pd.to_datetime(df['time'])

        # Today's bars only (midnight UTC to now)
        today = df['time'].iloc[-1].normalize()   # midnight of last bar's day
        today_bars = df[df['time'] >= today].copy()

        if len(today_bars) < 2:
            # Fallback to last 24 bars if session just opened
            today_bars = df.tail(24).copy()

        # Typical price = (H + L + C) / 3
        today_bars['typical'] = (today_bars['high'] + today_bars['low'] + today_bars['close']) / 3
        vol = today_bars['tick_volume'].clip(lower=1)

        cumvol   = vol.cumsum()
        cum_pv   = (today_bars['typical'] * vol).cumsum()
        cum_pv2  = (today_bars['typical'] ** 2 * vol).cumsum()

        vwap     = float((cum_pv / cumvol).iloc[-1])
        vwap_var = float((cum_pv2 / cumvol).iloc[-1]) - vwap ** 2
        vwap_sd  = float(np.sqrt(max(vwap_var, 0)))

        current_price = float(df['close'].iloc[-1])
        deviation     = (current_price - vwap) / vwap * 100   # % from VWAP

        # Band position: how many σ from VWAP
        sigma_dist = (current_price - vwap) / vwap_sd if vwap_sd > 0 else 0.0

        return {
            'vwap':         round(vwap, 2),
            'vwap_sd':      round(vwap_sd, 4),
            'current':      round(current_price, 2),
            'deviation_pct': round(deviation, 4),
            'sigma_dist':   round(float(sigma_dist), 3),
            'above_vwap':   current_price > vwap,
            'band_1up':     round(vwap + vwap_sd, 2),
            'band_1dn':     round(vwap - vwap_sd, 2),
            'band_2up':     round(vwap + 2 * vwap_sd, 2),
            'band_2dn':     round(vwap - 2 * vwap_sd, 2),
            'bars_used':    len(today_bars),
        }

    # ------------------------------------------------------------------
    # Signal alignment check
    # ------------------------------------------------------------------
    def check_signal(self, signal_type: str, df: pd.DataFrame) -> tuple:
        """
        Returns (allowed: bool, size_multiplier: float).

        Logic:
          BUY above VWAP  → aligned (+10% size)
          BUY below VWAP  → counter-flow (allowed but reduced -20%)
          SELL below VWAP → aligned (+10% size)
          SELL above VWAP → counter-flow (allowed but reduced -20%)

          Price beyond ±2σ → mean-reversion zone, slight counter-bias
          (at extreme extension, VWAP pull-back is likely)
        """
        try:
            v = self.compute(df)
        except Exception as e:
            logger.warning(f"VWAP: compute error — {e}")
            return True, 1.0

        above   = v['above_vwap']
        sigma   = v['sigma_dist']
        dev_pct = abs(v['deviation_pct'])

        # Neutral zone — price essentially at VWAP, no bias
        if dev_pct < self.NEUTRAL_BAND_PCT:
            logger.info(f"VWAP: neutral zone (price={v['current']}, VWAP={v['vwap']})")
            return True, 1.0

        if signal_type == 'BUY':
            if above:
                if sigma > 2.0:
                    # Over-extended above VWAP — partial fade risk
                    mult = 0.85
                    logger.info(f"VWAP: BUY aligned but >2σ above (σ={sigma:.2f}) — mult={mult}")
                else:
                    mult = 1.1
                    logger.info(f"VWAP: BUY confirmed above VWAP ({v['vwap']:.2f}) — mult={mult}")
            else:
                # Buying below VWAP — fighting institutional flow
                mult = 0.8
                logger.info(
                    f"VWAP: BUY below VWAP ({v['vwap']:.2f}, "
                    f"dev={v['deviation_pct']:.3f}%) — reduced mult={mult}"
                )

        elif signal_type == 'SELL':
            if not above:
                if sigma < -2.0:
                    mult = 0.85
                    logger.info(f"VWAP: SELL aligned but >2σ below (σ={sigma:.2f}) — mult={mult}")
                else:
                    mult = 1.1
                    logger.info(f"VWAP: SELL confirmed below VWAP ({v['vwap']:.2f}) — mult={mult}")
            else:
                mult = 0.8
                logger.info(
                    f"VWAP: SELL above VWAP ({v['vwap']:.2f}, "
                    f"dev={v['deviation_pct']:.3f}%) — reduced mult={mult}"
                )
        else:
            return True, 1.0

        return True, mult   # VWAP never hard-blocks — it adjusts size only
