"""
Monte Carlo Risk Engine
========================
- 10,000-path simulation via parametric + historical bootstrap
- Value at Risk (VaR) and Conditional VaR (CVaR/Expected Shortfall)
- Ruin probability
- Drawdown distribution
- Kelly fraction estimation
"""

import numpy as np
from logger import logger


class MonteCarloEngine:
    def __init__(self, n_paths: int = 10_000, seed: int = 42):
        self.n_paths = n_paths
        self.rng = np.random.default_rng(seed)

    # ------------------------------------------------------------------
    # Primary simulation: takes list of historical trade P&Ls
    # ------------------------------------------------------------------
    def simulate(self, trade_profits: list, initial_balance: float,
                 n_trades: int = None, ruin_threshold: float = 0.5) -> dict:
        """
        Run full Monte Carlo simulation on a sequence of trade P&Ls.

        Args:
            trade_profits   : List of historical trade profits (dollars)
            initial_balance : Starting account balance
            n_trades        : Future trades to simulate (default = len(trade_profits))
            ruin_threshold  : Account is 'ruined' if balance < initial * threshold (default 0.5 = 50%)
        """
        if len(trade_profits) < 5:
            logger.warning("Monte Carlo: insufficient trades (<5)")
            return {}

        arr = np.array(trade_profits, dtype=float)
        n = n_trades or len(arr)

        # Bootstrap: resample with replacement from historical trades
        samples = self.rng.choice(arr, size=(self.n_paths, n), replace=True)

        # Build equity paths
        cum_pnl = np.cumsum(samples, axis=1)
        paths = initial_balance + cum_pnl                         # shape (n_paths, n)
        paths = np.hstack([np.full((self.n_paths, 1), initial_balance), paths])

        final_balances = paths[:, -1]

        # Max drawdown per path
        running_max = np.maximum.accumulate(paths, axis=1)
        drawdowns_pct = (paths - running_max) / running_max       # negative values
        max_dds_pct = np.abs(drawdowns_pct.min(axis=1))

        # Returns
        returns_pct = (final_balances - initial_balance) / initial_balance * 100

        # Ruin
        ruin_mask = final_balances < initial_balance * ruin_threshold
        ruin_probability = float(ruin_mask.mean() * 100)

        # VaR & CVaR (on final balance)
        var_5 = float(np.percentile(returns_pct, 5))      # 5th percentile return
        cvar_5 = float(returns_pct[returns_pct <= var_5].mean()) if (returns_pct <= var_5).any() else var_5

        # Kelly fraction
        kelly = self._kelly_fraction(arr)

        result = {
            'n_paths': self.n_paths,
            'n_trades_simulated': n,
            'initial_balance': initial_balance,

            # Final balance distribution
            'median_final_balance': round(float(np.median(final_balances)), 2),
            'mean_final_balance': round(float(np.mean(final_balances)), 2),
            'p5_final_balance': round(float(np.percentile(final_balances, 5)), 2),
            'p25_final_balance': round(float(np.percentile(final_balances, 25)), 2),
            'p75_final_balance': round(float(np.percentile(final_balances, 75)), 2),
            'p95_final_balance': round(float(np.percentile(final_balances, 95)), 2),

            # Return distribution
            'median_return_pct': round(float(np.median(returns_pct)), 2),
            'var_5pct': round(var_5, 2),
            'cvar_5pct': round(cvar_5, 2),

            # Drawdown
            'median_max_drawdown_pct': round(float(np.median(max_dds_pct) * 100), 2),
            'p95_max_drawdown_pct': round(float(np.percentile(max_dds_pct, 95) * 100), 2),
            'worst_max_drawdown_pct': round(float(max_dds_pct.max() * 100), 2),

            # Ruin
            'ruin_probability_pct': round(ruin_probability, 2),
            'ruin_threshold_pct': ruin_threshold * 100,

            # Edge
            'kelly_fraction_pct': round(kelly * 100, 2),
            'recommended_risk_per_trade_pct': round(kelly * 25 * 100, 3),  # 25% of Kelly = conservative
        }

        self._log_summary(result)
        return result

    # ------------------------------------------------------------------
    # Parametric simulation (normal distribution fit)
    # ------------------------------------------------------------------
    def simulate_parametric(self, trade_profits: list, initial_balance: float,
                             n_trades: int = None) -> dict:
        arr = np.array(trade_profits, dtype=float)
        mu = float(np.mean(arr))
        sigma = float(np.std(arr))
        n = n_trades or len(arr)

        samples = self.rng.normal(mu, sigma, size=(self.n_paths, n))
        paths = initial_balance + np.cumsum(samples, axis=1)
        final = paths[:, -1]

        return {
            'parametric_mu': round(mu, 4),
            'parametric_sigma': round(sigma, 4),
            'parametric_median_final': round(float(np.median(final)), 2),
            'parametric_p5_final': round(float(np.percentile(final, 5)), 2),
            'parametric_p95_final': round(float(np.percentile(final, 95)), 2),
        }

    # ------------------------------------------------------------------
    # Kelly Criterion
    # f* = (W/L_avg - (1-W)/W_avg) ... simplified edge/odds version
    # ------------------------------------------------------------------
    def _kelly_fraction(self, profits: np.ndarray) -> float:
        wins = profits[profits > 0]
        losses = profits[profits < 0]

        if len(wins) == 0 or len(losses) == 0:
            return 0.01

        win_rate = len(wins) / len(profits)
        avg_win = float(np.mean(wins))
        avg_loss = float(abs(np.mean(losses)))

        if avg_loss == 0:
            return 0.01

        # Kelly: f* = W/L - (1-W)/W_size  => using R ratio
        r = avg_win / avg_loss
        kelly = win_rate - (1 - win_rate) / r
        return max(0.0, min(kelly, 0.25))  # Cap at 25% (full Kelly is dangerous)

    def _log_summary(self, r: dict):
        logger.info("=" * 55)
        logger.info("        MONTE CARLO ANALYSIS (%d paths)", r['n_paths'])
        logger.info("=" * 55)
        logger.info("  Balance distribution (end of %d trades):", r['n_trades_simulated'])
        logger.info("    5th  percentile : $%s", r['p5_final_balance'])
        logger.info("    25th percentile : $%s", r['p25_final_balance'])
        logger.info("    Median          : $%s", r['median_final_balance'])
        logger.info("    75th percentile : $%s", r['p75_final_balance'])
        logger.info("    95th percentile : $%s", r['p95_final_balance'])
        logger.info("  VaR (5%%)         : %s%% return", r['var_5pct'])
        logger.info("  CVaR (5%%)        : %s%% return", r['cvar_5pct'])
        logger.info("  Ruin prob (<%.0f%%): %.2f%%", r['ruin_threshold_pct'], r['ruin_probability_pct'])
        logger.info("  Max DD (median)  : %s%%", r['median_max_drawdown_pct'])
        logger.info("  Max DD (p95)     : %s%%", r['p95_max_drawdown_pct'])
        logger.info("  Kelly fraction   : %s%% (use %.3f%% per trade)",
                    r['kelly_fraction_pct'], r['recommended_risk_per_trade_pct'])
        logger.info("=" * 55)


if __name__ == "__main__":
    # Quick demo with synthetic trades
    rng = np.random.default_rng(0)
    sample_trades = list(rng.normal(8, 30, 50))  # avg $8 profit, $30 std, 50 trades

    mc = MonteCarloEngine(n_paths=10_000)
    result = mc.simulate(sample_trades, initial_balance=1000)

    print(f"Ruin prob     : {result['ruin_probability_pct']}%")
    print(f"Median return : {result['median_return_pct']}%")
    print(f"Kelly fraction: {result['kelly_fraction_pct']}%")
    print(f"Rec. risk/trade: {result['recommended_risk_per_trade_pct']}%")
