"""
MAE / MFE Analyzer
====================
Maximum Adverse Excursion  (MAE) — how far against you a trade went before
resolving. If winning trades had a MAE of 0.3×ATR on average, your 2×ATR
stop may be twice as wide as needed — you're giving back P&L unnecessarily.

Maximum Favorable Excursion (MFE) — how far in your favour a trade went
before closing. If losing trades hit 1.8×ATR profit before reversing to a
loss, your TP1 at 2×ATR is nearly always reachable and you should have
closed earlier.

Outputs:
  Recommended SL multiplier  — tighten or widen vs current Config.ATR_MULTIPLIER_SL
  Recommended TP1 multiplier — optimise the partial-close trigger
  Efficiency score           — what % of MFE you actually captured
"""

import numpy as np
import pandas as pd
from trade_database import TradeDatabase
from config import Config
from logger import logger


class MAEMFEAnalyzer:
    def __init__(self):
        self.db = TradeDatabase()

    # ------------------------------------------------------------------
    # Main analysis — needs MT5 bars to reconstruct price paths
    # ------------------------------------------------------------------
    def analyse(self, df_bars: pd.DataFrame, lookback_trades: int = 100) -> dict:
        """
        df_bars: M15 OHLC dataframe (used to reconstruct price path per trade)
        lookback_trades: how many closed trades to analyse
        """
        trades = self.db.get_all_trades(limit=lookback_trades)
        closed = [t for t in trades if t[14] == 'CLOSED' and t[6] is not None]

        if len(closed) < 10:
            logger.info(f"MAE/MFE: need 10+ closed trades, have {len(closed)}")
            return {}

        df_bars = df_bars.copy()
        df_bars['time'] = pd.to_datetime(df_bars['time'])
        df_bars = df_bars.sort_values('time').reset_index(drop=True)

        mae_list, mfe_list, efficiency_list = [], [], []
        win_mae, loss_mae, win_mfe, loss_mfe = [], [], [], []

        for t in closed:
            row = self._excursions_for_trade(t, df_bars)
            if row is None:
                continue
            mae, mfe, eff, is_win = row
            mae_list.append(mae)
            mfe_list.append(mfe)
            efficiency_list.append(eff)
            (win_mae if is_win else loss_mae).append(mae)
            (win_mfe if is_win else loss_mfe).append(mfe)

        if not mae_list:
            return {}

        result = self._build_result(
            mae_list, mfe_list, efficiency_list,
            win_mae, win_mfe, loss_mae, loss_mfe,
        )
        self._log_report(result)
        return result

    def _excursions_for_trade(self, t, df_bars: pd.DataFrame):
        """Return (mae, mfe, efficiency, is_win) for one trade row, or None."""
        ts_entry = pd.Timestamp(t[1])
        ts_close = pd.Timestamp(t[21]) if t[21] else None
        trade_type, entry_price, exit_price = t[4], t[5], t[6]
        profit = t[13] or t[10] or 0

        path = self._price_path(df_bars, ts_entry, ts_close)
        if path.empty:
            return None

        if trade_type == 'BUY':
            mae = float((entry_price - path['low'].min()) / entry_price * 100)
            mfe = float((path['high'].max() - entry_price) / entry_price * 100)
            captured = float((exit_price - entry_price) / entry_price * 100)
        else:
            mae = float((path['high'].max() - entry_price) / entry_price * 100)
            mfe = float((entry_price - path['low'].min()) / entry_price * 100)
            captured = float((entry_price - exit_price) / entry_price * 100)

        eff = (captured / mfe * 100) if mfe > 0 else 0
        return mae, mfe, eff, profit > 0

    def _price_path(self, df_bars: pd.DataFrame,
                    ts_entry: pd.Timestamp, ts_close) -> pd.DataFrame:
        """Slice df_bars between entry and close timestamps."""
        if ts_close:
            return df_bars[(df_bars['time'] >= ts_entry) & (df_bars['time'] <= ts_close)]
        entry_idx = df_bars.index[df_bars['time'] >= ts_entry]
        if len(entry_idx) == 0:
            return pd.DataFrame()
        start = entry_idx[0]
        return df_bars.iloc[start:min(start + 20, len(df_bars))]

    def _build_result(self, mae_list, mfe_list, efficiency_list,
                       win_mae, win_mfe, loss_mae, loss_mfe) -> dict:
        """Compute summary stats and recommendations from excursion lists."""
        p95_win_mae = np.percentile(win_mae, 95) if win_mae else None
        p50_loss_mfe = np.percentile(loss_mfe, 50) if loss_mfe else None
        return {
            'trades_analysed': len(mae_list),
            'avg_mae_pct': round(float(np.mean(mae_list)), 4),
            'avg_mfe_pct': round(float(np.mean(mfe_list)), 4),
            'avg_efficiency_pct': round(float(np.mean(efficiency_list)), 1),
            'p95_mae_pct': round(float(np.percentile(mae_list, 95)), 4),
            'p50_mfe_pct': round(float(np.percentile(mfe_list, 50)), 4),
            'wins_analysed': len(win_mae),
            'avg_win_mae_pct': round(float(np.mean(win_mae)), 4) if win_mae else 0,
            'avg_win_mfe_pct': round(float(np.mean(win_mfe)), 4) if win_mfe else 0,
            'losses_analysed': len(loss_mae),
            'avg_loss_mae_pct': round(float(np.mean(loss_mae)), 4) if loss_mae else 0,
            'avg_loss_mfe_pct': round(float(np.mean(loss_mfe)), 4) if loss_mfe else 0,
            'current_sl_mult': Config.ATR_MULTIPLIER_SL,
            'current_tp1_mult': getattr(Config, 'ATR_MULTIPLIER_TP1', 2.0),
            'recommended_sl_pct': round(p95_win_mae * 1.2, 4) if p95_win_mae else None,
            'recommended_tp1_pct': round(p50_loss_mfe * 0.9, 4) if p50_loss_mfe else None,
        }

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    def _log_report(self, r: dict):
        logger.info("=" * 55)
        logger.info("        MAE / MFE ANALYSIS (%d trades)", r['trades_analysed'])
        logger.info("=" * 55)
        logger.info("  Avg MAE       : %.4f%% (how far against you)", r['avg_mae_pct'])
        logger.info("  Avg MFE       : %.4f%% (how far in your favour)", r['avg_mfe_pct'])
        logger.info("  Efficiency    : %.1f%% of MFE captured", r['avg_efficiency_pct'])
        logger.info("  95th pct MAE  : %.4f%%", r['p95_mae_pct'])
        logger.info("-" * 55)
        logger.info("  Winners  — MAE: %.4f%%  MFE: %.4f%%",
                    r['avg_win_mae_pct'], r['avg_win_mfe_pct'])
        logger.info("  Losers   — MAE: %.4f%%  MFE: %.4f%%",
                    r['avg_loss_mae_pct'], r['avg_loss_mfe_pct'])
        logger.info("-" * 55)
        if r['recommended_sl_pct']:
            logger.info("  Rec. SL cover : %.4f%% of price", r['recommended_sl_pct'])
        if r['recommended_tp1_pct']:
            logger.info("  Rec. TP1 at   : %.4f%% of price "
                        "(median loser MFE × 0.9)", r['recommended_tp1_pct'])
        logger.info("  Current SL mult : %.1f × ATR", r['current_sl_mult'])
        logger.info("  Current TP1 mult: %.1f × ATR", r['current_tp1_mult'])
        logger.info("=" * 55)

        if r['avg_efficiency_pct'] < 50:
            logger.warning(
                "MAE/MFE: efficiency <50%% — "
                "consider moving TP1 closer or tightening trailing stop"
            )
        if r['avg_win_mae_pct'] > r['avg_loss_mae_pct'] * 0.8:
            logger.warning(
                "MAE/MFE: winning trades going almost as far against you as losers — "
                "stop may be too wide; consider tightening SL"
            )
