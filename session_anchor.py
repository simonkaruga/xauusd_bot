"""
Session Anchor
==============
Tracks intraday reference levels that refresh each day:

  Asian Range  — high/low of the 00:00–08:00 UTC consolidation session
  PDH / PDL    — previous day's high and low (strongest daily key levels)
  Weekly Open  — Monday's first-bar open (weekly directional anchor)

How each level is traded:

  Asian Breakout (London open, 08:00–12:00 UTC)
    Price breaks above Asian high → BUY trigger
    Price breaks below Asian low  → SELL trigger
    SL: other side of the Asian range + small buffer
    TP: 1.5–2.0 × Asian range size projected from breakout point

  PDH / PDL Reaction (any session)
    Price pulls back to PDH → SELL reaction (resistance)
    Price pulls back to PDL → BUY reaction  (support)
    SL: small buffer beyond the PDH/PDL level
    TP: ATR-based or next structural level

  Weekly Open
    Directional bias anchor — used to grade confidence of trend trades.
    Price above weekly open = mild bullish bias; below = mild bearish.
"""

import pandas as pd
from datetime import datetime, timezone
from logger import logger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SessionAnchor:
    def __init__(self):
        self._asian_high:  float | None = None
        self._asian_low:   float | None = None
        self._asian_range: float | None = None   # height of Asian box
        self._pdh:         float | None = None
        self._pdl:         float | None = None
        self._weekly_open: float | None = None
        self._last_date:   str | None   = None

    # ------------------------------------------------------------------
    # Update all anchor levels from the latest M15 bar window.
    # Safe to call every cycle — only recalculates when the date changes.
    # ------------------------------------------------------------------
    def update(self, df: pd.DataFrame) -> None:
        if df is None or len(df) < 32:
            return

        df_t = df.copy()
        df_t['time'] = pd.to_datetime(df_t['time'])

        today     = _utcnow().date()
        yesterday = (pd.Timestamp(_utcnow()) - pd.Timedelta(days=1)).date()

        today_str = str(today)
        if self._last_date == today_str and self._asian_high is not None:
            return   # already computed today

        # --- Asian range: today's bars from 00:00 to 07:59 UTC ---
        asian_bars = df_t[
            (df_t['time'].dt.date == today) &
            (df_t['time'].dt.hour < 8)
        ]
        if len(asian_bars) >= 4:   # at least 1 hour of data
            self._asian_high  = float(asian_bars['high'].max())
            self._asian_low   = float(asian_bars['low'].min())
            self._asian_range = self._asian_high - self._asian_low

        # --- PDH / PDL: yesterday's full session ---
        yest_bars = df_t[df_t['time'].dt.date == yesterday]
        if len(yest_bars) >= 16:   # at least 4 hours
            self._pdh = float(yest_bars['high'].max())
            self._pdl = float(yest_bars['low'].min())

        # --- Weekly open: first bar of the current week (Monday) ---
        days_since_mon = _utcnow().weekday()   # 0 = Monday
        monday_date = (pd.Timestamp(_utcnow()) - pd.Timedelta(days=days_since_mon)).date()
        mon_bars = df_t[df_t['time'].dt.date == monday_date]
        if len(mon_bars) > 0:
            self._weekly_open = float(mon_bars['open'].iloc[0])

        self._last_date = today_str
        self._log()

    def _log(self):
        ar = f"{self._asian_low:.2f}–{self._asian_high:.2f} ({self._asian_range:.2f})" \
            if self._asian_high else "N/A"
        logger.info(
            f"Session anchors | Asian: {ar} | "
            f"PDH: {self._pdh} PDL: {self._pdl} | WeeklyOpen: {self._weekly_open}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_anchors(self) -> dict:
        return {
            'asian_high':   self._asian_high,
            'asian_low':    self._asian_low,
            'asian_range':  self._asian_range,
            'pdh':          self._pdh,
            'pdl':          self._pdl,
            'weekly_open':  self._weekly_open,
        }

    @property
    def asian_range_valid(self) -> bool:
        return self._asian_high is not None and self._asian_low is not None
