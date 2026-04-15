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
        win_mae, loss_mae = [], []
        win_mfe, loss_mfe = [], []

        for t in closed:
            ts_entry = pd.Timestamp(t[1])    # entry timestamp
            ts_close = pd.Timestamp(t[21]) if t[21] else None
            trade_type = t[4]
            entry_price = t[5]
            exit_price = t[6]
            profit = t[13] or t[10] or 0

            # Find bars between entry and close
            if ts_close:
                mask = (df_bars['time'] >= ts_entry) & (df_bars['time'] <= ts_close)
            else:
                # Approximate: 20 bars after entry
                entry_idx = df_bars.index[df_bars['time'] >= ts_entry]
                if len(entry_idx) == 0:
                    continue
                start = entry_idx[0]
                mask = df_bars.index.isin(range(start, min(start + 20, len(df_bars))))

            path = df_bars[mask]
            if path.empty:
                continue

            if trade_type == 'BUY':
                mae = float((entry_price - path['low'].min()) / entry_price * 100)
                mfe = float((path['high'].max() - entry_price) / entry_price * 100)
                captured = float((exit_price - entry_price) / entry_price * 100)
            else:
                mae = float((path['high'].max() - entry_price) / entry_price * 100)
                mfe = float((entry_price - path['low'].min()) / entry_price * 100)
                captured = float((entry_price - exit_price) / entry_price * 100)

            mae_list.append(mae)
            mfe_list.append(mfe)
            eff = (captured / mfe * 100) if mfe > 0 else 0
            efficiency_list.append(eff)

            if profit > 0:
                win_mae.append(mae)
                win_mfe.append(mfe)
            else:
                loss_mae.append(mae)
                loss_mfe.append(mfe)

        if not mae_list:
            return {}

        # Current ATR-based distances (in price terms, approx)
        avg_atr_pct = df_bars['close'].pct_change().std() * 100 * 14  # rough ATR%

        # How far winning trades went against us (should be much less than SL)
        avg_win_mae = np.mean(win_mae) if win_mae else 0
        # How far losing trades went in our favour before reversing
        avg_loss_mfe = np.mean(loss_mfe) if loss_mfe else 0

        # Current SL as % of price (approx from config)
        current_sl_mult = Config.ATR_MULTIPLIER_SL
        current_tp1_mult = getattr(Config, 'ATR_MULTIPLIER_TP1', 2.0)

        # Recommendation: SL should cover 95th percentile MAE of winning trades
        # (i.e. don't get stopped out of winners)
        if win_mae:
            p95_win_mae = np.percentile(win_mae, 95)
            recommended_sl_pct = p95_win_mae * 1.2   # 20% buffer
        else:
            recommended_sl_pct = None

        # Recommendation: TP1 should be at typical MFE of losing trades
        # (lock in profit before the trade reverses)
        if loss_mfe:
            p50_loss_mfe = np.percentile(loss_mfe, 50)
            recommended_tp1_pct = p50_loss_mfe * 0.9   # just below median losing trade MFE
        else:
            recommended_tp1_pct = None

        result = {
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
            'avg_loss_mfe_pct': round(float(avg_loss_mfe), 4),

            'current_sl_mult': current_sl_mult,
            'current_tp1_mult': current_tp1_mult,
            'recommended_sl_pct': round(recommended_sl_pct, 4) if recommended_sl_pct else None,
            'recommended_tp1_pct': round(recommended_tp1_pct, 4) if recommended_tp1_pct else None,
        }

        self._log_report(result)
        return result

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
