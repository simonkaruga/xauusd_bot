"""
Walk-Forward Optimizer
========================
Professional quant-grade optimization with:
  - Walk-forward out-of-sample validation
  - Deflated Sharpe Ratio (penalises multiple testing)
  - Parameter sensitivity analysis (robustness check)
  - Overfitting detection: if OOS Sharpe << IS Sharpe, flag as overfit
"""

import os
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from itertools import combinations, product
from scipy.stats import norm
from backtester import Backtester
from config import Config
from logger import logger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

# Path where optimized params are written so they survive process restarts
_OPT_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.optimized')


class WalkForwardOptimizer:
    def __init__(self):
        self.backtester = Backtester()
        self.best_params = {}
        self.all_results = []

    # ------------------------------------------------------------------
    # Main optimizer
    # ------------------------------------------------------------------
    def optimize(self, symbol='XAUUSD', total_days=120, train_days=60, test_days=20,
                 initial_balance=None):

        initial_balance = initial_balance or Config.SIMULATED_BALANCE
        logger.info(f"Walk-forward optimization | train={train_days}d | test={test_days}d")

        _original = {
            'FAST_EMA':                   Config.FAST_EMA,
            'SLOW_EMA':                   Config.SLOW_EMA,
            'ATR_MULTIPLIER_SL':          Config.ATR_MULTIPLIER_SL,
            'ATR_MULTIPLIER_TP':          Config.ATR_MULTIPLIER_TP,
            'MACRO_CONVICTION_THRESHOLD': Config.MACRO_CONVICTION_THRESHOLD,
        }
        param_grid = {
            'FAST_EMA':                   [7, 9, 12],
            'SLOW_EMA':                   [18, 21, 26],
            'ATR_MULTIPLIER_SL':          [1.5, 2.0, 2.5],
            'ATR_MULTIPLIER_TP':          [4.0, 5.0, 6.0],
            'MACRO_CONVICTION_THRESHOLD': [0.30, 0.40, 0.50],
        }

        in_sample_results = self._run_grid_search(symbol, train_days, initial_balance, param_grid)

        if not in_sample_results:
            logger.error("No valid results from optimization")
            for k, v in _original.items():
                setattr(Config, k, v)
            return None

        best_is = self._select_by_deflated_sharpe(in_sample_results)
        logger.info(f"Best IS params (Deflated Sharpe): {best_is['params']}")
        logger.info(f"  IS Sharpe={best_is['is_sharpe']:.3f} | PF={best_is['is_pf']:.2f} "
                    f"| WR={best_is['is_wr']:.1f}% | DD={best_is['is_dd']:.1f}%")

        oos_result = self._validate_oos(best_is['params'], symbol, test_days, initial_balance,
                                          in_sample_results)
        if oos_result.get('overfit'):
            for k, v in _original.items():
                setattr(Config, k, v)
            return oos_result

        sensitivity = self.sensitivity_analysis(best_is['params'], in_sample_results, param_grid)
        cpcv = self.cpcv_validate(symbol, best_is['params'], total_days=total_days,
                                   initial_balance=initial_balance)

        self.best_params = best_is['params']
        self.all_results = in_sample_results
        for k, v in _original.items():
            setattr(Config, k, v)

        final = {
            'params': best_is['params'],
            'is_sharpe': round(best_is['is_sharpe'], 3),
            'oos_sharpe': round(oos_result['oos_sharpe'], 3),
            'is_profit_factor': best_is['is_pf'],
            'oos_profit_factor': oos_result['oos_pf'],
            'overfit': False,
            'sensitivity': sensitivity,
            'cpcv': cpcv,
        }
        logger.info("=" * 55)
        logger.info(f"  OOS Sharpe: {final['oos_sharpe']:.3f} | OOS PF: {final['oos_profit_factor']:.2f}")
        logger.info(f"  Sensitivity (robust): {sensitivity['is_robust']}")
        logger.info("=" * 55)
        return final

    def _run_grid_search(self, symbol: str, train_days: int,
                          initial_balance: float, param_grid: dict) -> list:
        """Run all parameter combinations in-sample. Returns list of result dicts."""
        keys = list(param_grid.keys())
        combos = list(product(*[param_grid[k] for k in keys]))
        total = len(combos)
        results = []

        for i, vals in enumerate(combos):
            params = dict(zip(keys, vals))

            # Skip EMA combos where fast >= slow
            fast = params.get('FAST_EMA', 0)
            slow = params.get('SLOW_EMA', 1)
            if fast >= slow:
                continue

            # Skip ATR combos where SL >= TP (can't have R:R > 1 otherwise)
            sl_mult = params.get('ATR_MULTIPLIER_SL', 0)
            tp_mult = params.get('ATR_MULTIPLIER_TP', 1)
            if sl_mult >= tp_mult:
                continue

            for k, v in params.items():
                setattr(Config, k, v)

            result = self.backtester.run(symbol, days=train_days,
                                         initial_balance=initial_balance)
            if not result or 'error' in result:
                continue

            results.append({
                'params': params,
                'is_sharpe': result.get('sharpe_ratio', 0),
                'is_pf': result.get('profit_factor', 0),
                'is_wr': result.get('win_rate', 0),
                'is_return': result.get('return_pct', 0),
                'is_dd': result.get('max_drawdown_pct', 100),
                'is_trades': result.get('total_trades', 0),
            })

            if (i + 1) % 10 == 0:
                logger.info(f"Grid search: {i+1}/{total} combinations tested")

        return results

    def _validate_oos(self, params: dict, symbol: str,
                       test_days: int, initial_balance: float,
                       in_sample_results: list = None) -> dict:
        """Run OOS backtest and check for overfitting. Returns result dict."""
        for k, v in params.items():
            setattr(Config, k, v)
        oos = self.backtester.run(symbol, days=test_days, initial_balance=initial_balance)
        oos_sharpe = oos.get('sharpe_ratio', 0) if oos and 'error' not in oos else 0
        oos_pf = oos.get('profit_factor', 0) if oos and 'error' not in oos else 0

        is_sharpe = next(
            (r['is_sharpe'] for r in (in_sample_results or self.all_results)
             if r.get('params') == params), 0
        )
        if is_sharpe > 0.5 and oos_sharpe < is_sharpe * 0.5:
            logger.warning(
                f"OVERFITTING DETECTED: IS Sharpe={is_sharpe:.3f}, OOS Sharpe={oos_sharpe:.3f}."
            )
            self._reset_defaults()
            return {'overfit': True, 'is_sharpe': is_sharpe, 'oos_sharpe': oos_sharpe}
        return {'overfit': False, 'oos_sharpe': oos_sharpe, 'oos_pf': oos_pf}

    # ------------------------------------------------------------------
    # Combinatorial Purged Cross-Validation (CPCV)
    # Lopez de Prado (2018) — "Advances in Financial Machine Learning"
    #
    # Why better than walk-forward:
    #   Walk-forward tests each parameter set on ONE OOS window.
    #   CPCV uses C(N, k) combinations of N groups, giving multiple
    #   independent OOS Sharpe samples per parameter set.
    #   The resulting distribution tells you whether performance is
    #   consistent (robust) or lucky (wide variance).
    #
    # n_groups=6, n_test=2 → C(6,2)=15 OOS paths covering 2/6=33% of data
    # each, overlapping in training so every bar is OOS in multiple paths.
    # ------------------------------------------------------------------
    def _fetch_cpcv_data(self, symbol: str, total_days: int) -> tuple:
        """Connect (if needed), fetch M15 + M1 data, disconnect. Returns (df_m15, df_m1)."""
        connector = self.backtester.connector
        connected_here = False
        if not connector.is_connected():
            if not connector.connect():
                logger.error("CPCV: MT5 connection failed")
                return None, None
            connected_here = True
        try:
            df_m15 = connector.get_bars(symbol, Config.TIMEFRAME, total_days * 24 * 4)
            if df_m15 is None or len(df_m15) < 200:
                logger.error("CPCV: insufficient M15 data")
                return None, None
            df_m1 = connector.get_bars(symbol, 'M1', min(total_days * 24 * 60, 99_000))
        finally:
            if connected_here:
                connector.disconnect()
        return df_m15, df_m1

    def _align_m1_to_groups(self, df_m1, groups: list) -> list:
        """Slice M1 bars to match each M15 group's time window."""
        m1_groups = [None] * len(groups)
        if df_m1 is None or len(df_m1) == 0:
            return m1_groups
        df_m1 = df_m1.copy()
        df_m1['time'] = pd.to_datetime(df_m1['time'])
        for gi, g in enumerate(groups):
            t_start = pd.Timestamp(g.iloc[0]['time'])
            t_end   = pd.Timestamp(g.iloc[-1]['time'])
            sl = df_m1[(df_m1['time'] >= t_start) & (df_m1['time'] <= t_end)]
            m1_groups[gi] = sl if len(sl) > 0 else None
        return m1_groups

    def _log_cpcv_result(self, result: dict) -> None:
        logger.info("=" * 55)
        logger.info("  CPCV RESULTS (%d paths)", result['n_paths'])
        logger.info("  Median OOS Sharpe : %.3f", result['median_oos_sharpe'])
        logger.info("  P25 OOS Sharpe    : %.3f  (pessimistic)", result['p25_oos_sharpe'])
        logger.info("  P75 OOS Sharpe    : %.3f  (optimistic)", result['p75_oos_sharpe'])
        logger.info("  Consistency       : %.1f%% paths profitable", result['consistency_pct'])
        logger.info("  Robust            : %s", result['is_robust'])
        if not result['is_robust']:
            logger.warning(
                "CPCV: strategy is NOT robust — performance varies widely across "
                "paths. Consider widening param ranges or reducing complexity."
            )
        logger.info("=" * 55)

    def cpcv_validate(self, symbol: str = 'XAUUSD', params: dict = None,
                      total_days: int = 120, n_groups: int = 6, n_test: int = 2,
                      initial_balance: float = None) -> dict:
        """
        Fetch data once, divide into n_groups, run C(n_groups, n_test)
        IS/OOS backtests. Returns distribution of OOS Sharpe ratios.
        """
        params = params or self.best_params
        if not params:
            logger.warning("CPCV: no params supplied, using current Config values")
        if params:
            for k, v in params.items():
                setattr(Config, k, v)
        initial_balance = initial_balance or Config.SIMULATED_BALANCE

        logger.info(f"CPCV: fetching {total_days}d of M15 data for {symbol}")
        df_m15, df_m1 = self._fetch_cpcv_data(symbol, total_days)
        if df_m15 is None:
            return {}

        group_size = len(df_m15) // n_groups
        groups    = [df_m15.iloc[i * group_size:(i + 1) * group_size].copy()
                     for i in range(n_groups)]
        m1_groups = self._align_m1_to_groups(df_m1, groups)

        test_combos = list(combinations(range(n_groups), n_test))
        logger.info(f"CPCV: {n_groups} groups × C({n_groups},{n_test})={len(test_combos)} paths")

        oos_sharpes, is_sharpes = [], []
        for combo in test_combos:
            train_idx    = [i for i in range(n_groups) if i not in set(combo)]
            df_is        = pd.concat([groups[i]    for i in train_idx], ignore_index=True)
            df_oos       = pd.concat([groups[i]    for i in combo],     ignore_index=True)
            m1_is_parts  = [m1_groups[i] for i in train_idx if m1_groups[i] is not None]
            m1_oos_parts = [m1_groups[i] for i in combo     if m1_groups[i] is not None]
            m1_is  = pd.concat(m1_is_parts,  ignore_index=True) if m1_is_parts  else None
            m1_oos = pd.concat(m1_oos_parts, ignore_index=True) if m1_oos_parts else None
            r_is  = self.backtester.run_on_df(df_is,  m1_is,  initial_balance)
            r_oos = self.backtester.run_on_df(df_oos, m1_oos, initial_balance)
            if r_is  and 'error' not in r_is:
                is_sharpes.append(r_is.get('sharpe_ratio', 0))
            if r_oos and 'error' not in r_oos:
                oos_sharpes.append(r_oos.get('sharpe_ratio', 0))

        if not oos_sharpes:
            logger.error("CPCV: no valid OOS results")
            return {}

        arr        = np.array(oos_sharpes)
        p25_oos    = float(np.percentile(arr, 25))
        consistency = float((arr > 0).mean() * 100)
        result = {
            'oos_sharpes':       [round(s, 3) for s in oos_sharpes],
            'is_sharpes':        [round(s, 3) for s in is_sharpes],
            'median_oos_sharpe': round(float(np.median(arr)), 3),
            'p25_oos_sharpe':    round(p25_oos, 3),
            'p75_oos_sharpe':    round(float(np.percentile(arr, 75)), 3),
            'consistency_pct':   round(consistency, 1),
            'n_paths':           len(oos_sharpes),
            'is_robust':         (p25_oos > 0) and (consistency >= 60.0),
        }
        self._log_cpcv_result(result)
        return result

    # ------------------------------------------------------------------
    # Deflated Sharpe Ratio
    # Corrects for the bias introduced by testing many strategies
    # Reference: Bailey, Borwein, Lopez de Prado, Zhu (2014)
    # ------------------------------------------------------------------
    def _deflated_sharpe(self, observed_sharpe: float, n_trials: int,
                          n_obs: int, sharpe_std: float = None) -> float:
        if n_obs < 5:
            return 0.0

        # Expected maximum Sharpe under null (no edge) from multiple testing
        e_max = self._expected_max_sharpe(n_trials, n_obs)

        # Variance of Sharpe estimate
        if sharpe_std is None:
            sharpe_std = 1.0 / np.sqrt(n_obs)

        if sharpe_std == 0:
            return 0.0

        z = (observed_sharpe - e_max) / sharpe_std
        dsr = float(norm.cdf(z))
        return dsr

    def _expected_max_sharpe(self, n_trials: int, n_obs: int) -> float:
        if n_trials <= 1 or n_obs < 5:
            return 0.0
        euler_gamma = 0.5772156649
        e_max = (1 - euler_gamma) * norm.ppf(1 - 1.0 / n_trials) + \
                euler_gamma * norm.ppf(1 - 1.0 / (n_trials * np.e))
        return float(e_max / np.sqrt(n_obs))

    def _select_by_deflated_sharpe(self, results: list) -> dict:
        n_trials = len(results)
        # Approximate n_obs from trade counts
        all_sharpes = [r['is_sharpe'] for r in results]
        sharpe_std = float(np.std(all_sharpes)) if len(all_sharpes) > 1 else 1.0

        scored = []
        for r in results:
            n_obs = max(r['is_trades'], 5)
            dsr = self._deflated_sharpe(r['is_sharpe'], n_trials, n_obs, sharpe_std)
            # Combined score: DSR weighted with drawdown penalty
            dd_penalty = max(0, 1 - r['is_dd'] / 50)  # penalise > 50% DD
            score = dsr * dd_penalty
            scored.append({**r, 'dsr': dsr, 'score': score})

        best = max(scored, key=lambda x: x['score'])
        return best

    # ------------------------------------------------------------------
    # Parameter sensitivity: robust if neighbours are also good
    # ------------------------------------------------------------------
    def sensitivity_analysis(self, best_params: dict, all_results: list,
                              param_grid: dict) -> dict:
        """
        For each parameter in best_params, test ±1 step neighbours.
        If Sharpe drops > 40% when moving one step, the strategy is fragile.
        """
        best_key = tuple(sorted(best_params.items()))
        best_result = next((r for r in all_results
                            if tuple(sorted(r['params'].items())) == best_key), None)

        if not best_result:
            return {'is_robust': False, 'details': {}}

        best_sharpe = best_result['is_sharpe']
        details = {}
        fragile_params = []

        for param, values in param_grid.items():
            best_val = best_params.get(param)
            if best_val is None or best_val not in values:
                continue

            idx = values.index(best_val)
            neighbour_sharpes = []

            for offset in [-1, 1]:
                ni = idx + offset
                if 0 <= ni < len(values):
                    neighbour_val = values[ni]
                    modified = {**best_params, param: neighbour_val}
                    key = tuple(sorted(modified.items()))
                    neighbour = next((r for r in all_results
                                      if tuple(sorted(r['params'].items())) == key), None)
                    if neighbour:
                        neighbour_sharpes.append(neighbour['is_sharpe'])

            if neighbour_sharpes:
                avg_neighbour = np.mean(neighbour_sharpes)
                drop_pct = (best_sharpe - avg_neighbour) / (abs(best_sharpe) + 1e-9) * 100
                details[param] = {
                    'best_val': best_val,
                    'neighbour_sharpes': [round(s, 3) for s in neighbour_sharpes],
                    'avg_neighbour_sharpe': round(float(avg_neighbour), 3),
                    'drop_pct': round(float(drop_pct), 1),
                    'fragile': drop_pct > 40,
                }
                if drop_pct > 40:
                    fragile_params.append(param)

        is_robust = len(fragile_params) == 0

        if fragile_params:
            logger.warning(f"Fragile parameters (sensitivity > 40%): {fragile_params}")
        else:
            logger.info("Strategy is robust — all parameters pass sensitivity check")

        return {'is_robust': is_robust, 'fragile_params': fragile_params, 'details': details}

    # ------------------------------------------------------------------
    # Apply and reset
    # ------------------------------------------------------------------
    def apply_best_params(self):
        if self.best_params:
            for k, v in self.best_params.items():
                setattr(Config, k, v)
            logger.info(f"Optimized parameters applied: {self.best_params}")
            self._persist_params(self.best_params)

    def _persist_params(self, params: dict):
        """Write optimized params to .env.optimized for automatic reload on restart."""
        lines = [
            "# Auto-generated by WalkForwardOptimizer — do not edit manually",
            f"# Generated: {_utcnow().isoformat()}",
            "",
        ]
        for k, v in params.items():
            lines.append(f"{k}={v}")

        with open(_OPT_ENV_PATH, 'w') as f:
            f.write('\n'.join(lines) + '\n')

        logger.info(f"Optimized parameters persisted → {_OPT_ENV_PATH}")

    def _reset_defaults(self):
        Config.FAST_EMA = 9
        Config.SLOW_EMA = 21
        Config.ATR_MULTIPLIER_SL = 2.0
        Config.ATR_MULTIPLIER_TP = 5.0   # must match MIN_RISK_REWARD=2.5 (5/2=2.5R)
        Config.MACRO_CONVICTION_THRESHOLD = 0.40
        logger.info("Reset to default parameters (overfit detected)")

    # ------------------------------------------------------------------
    # Monte Carlo bootstrap: Sharpe confidence interval
    #
    # Resamples the trade P&L series 1000× with replacement and computes
    # the Sharpe distribution. Gives a 90% CI — if the lower bound is
    # still positive, the edge is statistically robust.
    # ------------------------------------------------------------------
    def monte_carlo_sharpe_ci(self, trade_pnls: list,
                               n_iter: int = 1000,
                               n_per_year: int = 600) -> dict:
        """
        Bootstrap Sharpe confidence interval from a list of trade P&Ls.

        Returns:
          mean_sharpe   : mean of bootstrap distribution
          sharpe_p5     : 5th percentile (pessimistic — use for go/no-go decision)
          sharpe_p50    : median
          sharpe_p95    : 95th percentile (optimistic)
          positive_pct  : % of bootstrap samples with Sharpe > 0
        """
        if len(trade_pnls) < 10:
            logger.warning("Monte Carlo: need ≥ 10 trades for CI")
            return {}

        arr = np.array(trade_pnls, dtype=float)
        rng = np.random.default_rng(seed=42)
        sharpes = []

        for _ in range(n_iter):
            sample = rng.choice(arr, size=len(arr), replace=True)
            std = sample.std()
            if std == 0:
                continue
            sharpes.append(float(sample.mean() / std * np.sqrt(n_per_year)))

        if not sharpes:
            return {}

        sharpes = np.array(sharpes)
        result = {
            'mean_sharpe':  round(float(sharpes.mean()), 3),
            'sharpe_p5':    round(float(np.percentile(sharpes, 5)), 3),
            'sharpe_p50':   round(float(np.percentile(sharpes, 50)), 3),
            'sharpe_p95':   round(float(np.percentile(sharpes, 95)), 3),
            'positive_pct': round(float((sharpes > 0).mean() * 100), 1),
            'n_trades':     len(trade_pnls),
            'n_iter':       n_iter,
        }
        logger.info(
            "Monte Carlo Sharpe CI (%d trades, %d iter): "
            "p5=%.3f | median=%.3f | p95=%.3f | %.1f%% positive",
            result['n_trades'], n_iter,
            result['sharpe_p5'], result['sharpe_p50'],
            result['sharpe_p95'], result['positive_pct']
        )
        return result


if __name__ == "__main__":
    opt = WalkForwardOptimizer()
    result = opt.optimize(total_days=120, train_days=60, test_days=20)
    if result and not result.get('overfit'):
        opt.apply_best_params()
        print(f"IS Sharpe: {result['is_sharpe']} | OOS Sharpe: {result['oos_sharpe']}")
        print(f"Robust: {result['sensitivity']['is_robust']}")
