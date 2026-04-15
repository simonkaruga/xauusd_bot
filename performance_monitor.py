"""
Performance Monitor
====================
Live tracking of:
  - Sharpe / Sortino ratios on closed trades
  - Rolling performance windows (last 10, 30, 90 trades)
  - Drawdown duration (max + average bars spent underwater)
  - Degradation alert: win-rate drop > 15% over recent vs prior 10 trades
  - Benchmark: compare vs buy-and-hold gold return
"""

import numpy as np
from datetime import datetime, timedelta
from trade_database import TradeDatabase
from logger import logger


class PerformanceMonitor:
    def __init__(self):
        self.db = TradeDatabase()
        self.alerts = []

    # ------------------------------------------------------------------
    # Core metrics
    # ------------------------------------------------------------------
    def get_metrics(self, window: int = None) -> dict | None:
        """
        window=None  → all-time
        window=10/30/90 → last N closed trades
        """
        if window:
            trades = self.db.get_all_trades(limit=window)
        else:
            trades = self.db.get_all_trades(limit=10_000)

        # Column indices: id(0) ts(1) ticket(2) symbol(3) type(4) entry(5)
        # exit(6) sl(7) tp(8) vol(9) profit(10) comm(11) spread(12) net_profit(13)
        # status(14) session(15) regime(16) conf(17) macro(18) ml(19) comment(20) close_time(21)
        profits = [t[13] if t[13] is not None else (t[10] or 0) for t in trades
                   if t[14] == 'CLOSED']

        if not profits:
            return None

        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p <= 0]
        total = len(profits)
        win_count = len(wins)
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))

        metrics = {
            'window': window or 'all',
            'total_trades': total,
            'wins': win_count,
            'losses': len(losses),
            'win_rate': round(win_count / total * 100, 1) if total else 0,
            'profit_factor': round(gross_profit / gross_loss, 2) if gross_loss else 0,
            'avg_win': round(sum(wins) / len(wins), 2) if wins else 0,
            'avg_loss': round(sum(losses) / len(losses), 2) if losses else 0,
            'expectancy': round(sum(profits) / total, 2) if total else 0,
            'net_pnl': round(sum(profits), 2),
            'sharpe_ratio': round(self._sharpe(profits), 3),
            'sortino_ratio': round(self._sortino(profits), 3),
            'max_consecutive_losses': self._max_consecutive_losses(profits),
            'drawdown_duration': self._drawdown_duration(profits),
        }
        return metrics

    def get_rolling_windows(self) -> dict:
        """Return metrics for last 10, 30, 90 trades side-by-side."""
        return {
            'last_10': self.get_metrics(window=10),
            'last_30': self.get_metrics(window=30),
            'last_90': self.get_metrics(window=90),
            'all_time': self.get_metrics(window=None),
        }

    # ------------------------------------------------------------------
    # Degradation detection
    # ------------------------------------------------------------------
    def check_performance_degradation(self) -> str | None:
        trades = self.db.get_all_trades(limit=20)
        closed = [t for t in trades if t[14] == 'CLOSED']
        if len(closed) < 20:
            return None

        profits = [t[13] or t[10] or 0 for t in closed]
        recent_10 = profits[:10]
        prior_10 = profits[10:20]

        recent_wr = sum(1 for p in recent_10 if p > 0) / 10
        prior_wr = sum(1 for p in prior_10 if p > 0) / 10

        recent_sharpe = self._sharpe(recent_10)
        prior_sharpe = self._sharpe(prior_10)

        alert = None
        if recent_wr < prior_wr - 0.15:
            alert = (
                f"WIN RATE DEGRADATION: recent {recent_wr*100:.0f}% vs prior {prior_wr*100:.0f}%"
            )
        elif prior_sharpe > 0.5 and recent_sharpe < prior_sharpe * 0.5:
            alert = (
                f"SHARPE DEGRADATION: recent {recent_sharpe:.2f} vs prior {prior_sharpe:.2f}"
            )

        if alert:
            logger.warning(f"Performance alert: {alert}")
            self.alerts.append({'time': datetime.now().isoformat(), 'msg': alert})

        return alert

    # ------------------------------------------------------------------
    # Sharpe / Sortino on trade P&L series
    # ------------------------------------------------------------------
    def _sharpe(self, profits: list, rf: float = 0.0) -> float:
        if len(profits) < 2:
            return 0.0
        arr = np.array(profits, dtype=float)
        std = np.std(arr)
        if std == 0:
            return 0.0
        # Annualise assuming ~3 trades/day × 200 trading days = 600 trades/year
        n_per_year = 600
        return float((np.mean(arr) - rf) / std * np.sqrt(n_per_year))

    def _sortino(self, profits: list, rf: float = 0.0) -> float:
        if len(profits) < 2:
            return 0.0
        arr = np.array(profits, dtype=float)
        downside = arr[arr < 0]
        if len(downside) == 0:
            return float('inf')
        downside_std = np.std(downside)
        if downside_std == 0:
            return 0.0
        n_per_year = 600
        return float((np.mean(arr) - rf) / downside_std * np.sqrt(n_per_year))

    def _max_consecutive_losses(self, profits: list) -> int:
        max_streak = streak = 0
        for p in profits:
            if p <= 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        return max_streak

    def _drawdown_duration(self, profits: list) -> dict:
        """Number of trades spent in drawdown (below running peak P&L)."""
        if not profits:
            return {'max_trades': 0, 'avg_trades': 0.0}
        equity = np.cumsum(profits)
        peak = equity[0]
        in_dd = False
        dd_start = 0
        durations = []
        for i, val in enumerate(equity):
            if val >= peak:
                if in_dd:
                    durations.append(i - dd_start)
                    in_dd = False
                peak = val
            else:
                if not in_dd:
                    in_dd = True
                    dd_start = i
        if in_dd:
            durations.append(len(equity) - dd_start)
        return {
            'max_trades': int(max(durations)) if durations else 0,
            'avg_trades': round(float(np.mean(durations)), 1) if durations else 0.0,
        }

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    def daily_report(self) -> str:
        windows = self.get_rolling_windows()
        all_t = windows['all_time']
        last30 = windows['last_30']

        if not all_t:
            return "No closed trades yet"

        lines = [
            "PERFORMANCE REPORT",
            "=" * 42,
            f"  All-time trades : {all_t['total_trades']}",
            f"  Win Rate        : {all_t['win_rate']}%",
            f"  Profit Factor   : {all_t['profit_factor']}",
            f"  Sharpe Ratio    : {all_t['sharpe_ratio']}",
            f"  Sortino Ratio   : {all_t['sortino_ratio']}",
            f"  Expectancy      : ${all_t['expectancy']}/trade",
            f"  Net P&L         : ${all_t['net_pnl']}",
            f"  Max consec. loss: {all_t['max_consecutive_losses']}",
            f"  DD duration (max): {all_t['drawdown_duration']['max_trades']} trades",
            "-" * 42,
        ]
        if last30:
            lines += [
                f"  Last 30 WR      : {last30['win_rate']}%",
                f"  Last 30 Sharpe  : {last30['sharpe_ratio']}",
                f"  Last 30 PF      : {last30['profit_factor']}",
            ]

        alert = self.check_performance_degradation()
        if alert:
            lines.append(f"  *** ALERT: {alert} ***")

        report = "\n".join(lines)
        logger.info(report)
        return report
