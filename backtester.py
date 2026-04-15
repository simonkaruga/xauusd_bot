import pandas as pd
import numpy as np
from datetime import datetime
from mt5_connector import MT5Connector
from advanced_strategy import AdvancedStrategy
from config import Config
from logger import logger


# XAU/USD realistic execution costs
SPREAD_POINTS = 0.35        # $0.35/oz typical ECN spread
COMMISSION_PER_LOT = 7.0    # $7 roundtrip per lot (ECN standard)
CONTRACT_SIZE = 100          # oz per lot

# Swap / overnight rollover costs (charged at 22:00 UTC each trading day)
# XAU/USD is effectively a gold loan vs USD: both sides typically negative
SWAP_LONG_PER_LOT  = -5.0   # USD per lot per night (long gold)
SWAP_SHORT_PER_LOT = -3.0   # USD per lot per night (short gold)
SWAP_HOUR_UTC = 22           # Rollover time — 22:00 UTC server time

# News spread widening: broker spreads blow out 3-5× during high-impact events
NEWS_SPREAD_MULTIPLIER = 4.0  # 4× during FOMC/NFP/CPI windows


class Backtester:
    def __init__(self):
        self.connector = MT5Connector()
        # backtest_mode=True bypasses real-clock _check_session/_check_cooldown
        # so historical bars aren't silently dropped based on current wall time
        self.strategy = AdvancedStrategy(backtest_mode=True)
        self.trades = []

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def run(self, symbol='XAUUSD', days=30, initial_balance=1000, include_costs=True,
            use_m1_simulation=True):
        """
        use_m1_simulation=True: fetches M1 bars for SL/TP checks (accurate),
        but generates signals on M15 bars (strategy timeframe).
        This catches SL/TP hits that happen mid M15-bar — the biggest source
        of backtest optimism in bar-level simulators.
        """
        if not self.connector.connect():
            logger.error("Failed to connect for backtesting")
            return None

        logger.info(
            f"Backtest: {symbol} | {days}d | ${initial_balance} | "
            f"costs={include_costs} | m1_sim={use_m1_simulation}"
        )

        # Signal generation bars (M15)
        m15_needed = days * 24 * 4
        df_m15 = self.connector.get_bars(symbol, Config.TIMEFRAME, m15_needed)
        if df_m15 is None or len(df_m15) < 100:
            logger.error("Insufficient M15 data")
            self.connector.disconnect()
            return None

        # High-resolution bars for SL/TP simulation (M1)
        df_sim = df_m15  # fallback
        if use_m1_simulation:
            m1_needed = days * 24 * 60
            df_m1 = self.connector.get_bars(symbol, 'M1', min(m1_needed, 99_000))
            if df_m1 is not None and len(df_m1) > 500:
                df_sim = df_m1
                logger.info(f"M1 simulation: {len(df_m1)} bars loaded")
            else:
                logger.warning("M1 data unavailable — falling back to M15 simulation")

        self.trades = []
        balance = initial_balance
        equity_curve = [initial_balance]
        position = None

        # Index M1 bars by timestamp for fast lookup
        sim_by_time = None
        if df_sim is not df_m15:
            df_sim = df_sim.copy()
            df_sim['time'] = df_sim['time'].astype('int64') // 10**9
            sim_by_time = df_sim.set_index('time')

        for i in range(100, len(df_m15)):
            window = df_m15.iloc[:i + 1].copy()
            m15_bar = window.iloc[-1]
            m15_time = int(pd.Timestamp(m15_bar['time']).timestamp())

            # --- SL/TP check at M1 resolution ---
            if position:
                if sim_by_time is not None:
                    # Find all M1 bars within this M15 bar's 15-minute window
                    end_time = m15_time + 899  # 14:59 into the candle
                    start_time = m15_time
                    m1_slice = sim_by_time.loc[
                        (sim_by_time.index >= start_time) &
                        (sim_by_time.index <= end_time)
                    ]
                    hit, exit_price = self._check_sl_tp_m1(position, m1_slice)
                else:
                    hit, exit_price = self._check_sl_tp(position, m15_bar)

                if hit:
                    pnl = self._calc_pnl(position, exit_price, include_costs,
                                         exit_time=m15_bar['time'])
                    balance += pnl
                    self.trades.append({**position, 'exit': exit_price, 'profit': pnl,
                                        'exit_bar': i, 'exit_time': m15_bar['time']})
                    position = None
                    equity_curve.append(balance)
                    continue

            # --- Generate signal on M15 bars (session-filtered by bar time) ---
            if not position and self._bar_in_session(m15_bar['time']):
                signal = self.strategy.generate_signal(window)
                if signal:
                    risk_amount = balance * Config.RISK_PER_TRADE
                    price_diff = abs(signal['price'] - signal['sl'])
                    if price_diff == 0:
                        continue
                    reward = abs(signal['tp'] - signal['price'])
                    if (reward / price_diff) < Config.MIN_RISK_REWARD:
                        continue
                    size = max(0.01, round(risk_amount / (price_diff * CONTRACT_SIZE), 2))

                    # Apply market-impact slippage to entry price
                    atr = signal.get('atr', 0)
                    fill_price = self._apply_entry_slippage(
                        signal['price'], signal['type'], atr, m15_bar['time']
                    )

                    position = {
                        'type': signal['type'],
                        'entry': fill_price,
                        'sl': signal['sl'],
                        'tp': signal['tp'],
                        'size': size,
                        'entry_bar': i,
                        'entry_time': m15_bar['time'],
                        'atr': atr,
                        'regime': signal.get('regime', 'unknown'),
                        'session': self._classify_session(m15_bar['time']),
                    }

            equity_curve.append(balance)

        if position:
            last_bar = df_m15.iloc[-1]
            pnl = self._calc_pnl(position, last_bar['close'], include_costs,
                                  exit_time=last_bar['time'])
            balance += pnl
            self.trades.append({**position, 'exit': last_bar['close'], 'profit': pnl,
                                 'exit_bar': len(df_m15) - 1, 'exit_time': last_bar['time']})
            equity_curve.append(balance)

        self.connector.disconnect()
        return self._report(initial_balance, balance, equity_curve)

    # ------------------------------------------------------------------
    # Run backtest on pre-fetched dataframes (used by CPCV — no MT5 call)
    # ------------------------------------------------------------------
    def run_on_df(self, df_m15: pd.DataFrame, df_m1: pd.DataFrame = None,
                  initial_balance: float = 1000, include_costs: bool = True) -> dict:
        """
        Run the full simulation on already-fetched data slices.
        Called by WalkForwardOptimizer.cpcv_validate() so that data is
        fetched once and reused across all C(N,k) fold combinations.
        """
        if df_m15 is None or len(df_m15) < 100:
            return {'error': 'Insufficient data'}

        self.trades = []
        balance = initial_balance
        equity_curve = [initial_balance]
        position = None

        sim_by_time = None
        if df_m1 is not None and len(df_m1) > 0:
            df_s = df_m1.copy()
            df_s['time'] = pd.to_datetime(df_s['time'])
            df_s['_ts'] = df_s['time'].astype('int64') // 10 ** 9
            sim_by_time = df_s.set_index('_ts')

        for i in range(100, len(df_m15)):
            window = df_m15.iloc[:i + 1].copy()
            m15_bar = window.iloc[-1]
            m15_time = int(pd.Timestamp(m15_bar['time']).timestamp())

            if position:
                if sim_by_time is not None:
                    m1_slice = sim_by_time.loc[
                        (sim_by_time.index >= m15_time) &
                        (sim_by_time.index <= m15_time + 899)
                    ]
                    hit, exit_price = self._check_sl_tp_m1(position, m1_slice)
                else:
                    hit, exit_price = self._check_sl_tp(position, m15_bar)

                if hit:
                    pnl = self._calc_pnl(position, exit_price, include_costs,
                                         exit_time=m15_bar['time'])
                    balance += pnl
                    self.trades.append({**position, 'exit': exit_price, 'profit': pnl,
                                        'exit_bar': i, 'exit_time': m15_bar['time']})
                    position = None
                    equity_curve.append(balance)
                    continue

            if not position:
                signal = self.strategy.generate_signal(window)
                if signal:
                    risk_amount = balance * Config.RISK_PER_TRADE
                    price_diff = abs(signal['price'] - signal['sl'])
                    if price_diff == 0:
                        continue
                    reward = abs(signal['tp'] - signal['price'])
                    if (reward / price_diff) < Config.MIN_RISK_REWARD:
                        continue
                    size = max(0.01, round(risk_amount / (price_diff * CONTRACT_SIZE), 2))

                    # Apply market-impact slippage to entry price
                    atr = signal.get('atr', 0)
                    fill_price = self._apply_entry_slippage(
                        signal['price'], signal['type'], atr, m15_bar['time']
                    )

                    position = {
                        'type': signal['type'],
                        'entry': fill_price,
                        'sl': signal['sl'],
                        'tp': signal['tp'],
                        'size': size,
                        'entry_bar': i,
                        'entry_time': m15_bar['time'],
                        'atr': atr,
                        'regime': signal.get('regime', 'unknown'),
                        'session': self._classify_session(m15_bar['time']),
                    }

            equity_curve.append(balance)

        if position:
            last_bar = df_m15.iloc[-1]
            pnl = self._calc_pnl(position, last_bar['close'], include_costs,
                                  exit_time=last_bar['time'])
            balance += pnl
            self.trades.append({**position, 'exit': last_bar['close'], 'profit': pnl,
                                 'exit_bar': len(df_m15) - 1,
                                 'exit_time': last_bar['time']})
            equity_curve.append(balance)

        return self._report(initial_balance, balance, equity_curve)

    # ------------------------------------------------------------------
    # M1-resolution SL/TP check: walk each M1 bar inside the M15 window
    # ------------------------------------------------------------------
    def _check_sl_tp_m1(self, position, m1_slice: pd.DataFrame):
        """Walk M1 bars in order. First hit wins (SL or TP)."""
        for _, bar in m1_slice.iterrows():
            hit, price = self._check_sl_tp(position, bar)
            if hit:
                return hit, price
        return False, None

    # ------------------------------------------------------------------
    # Bar simulation: check if SL or TP was hit within the bar
    # Professional approach: check worst-case first (assume SL hits before TP)
    # ------------------------------------------------------------------
    def _check_sl_tp(self, position, bar):
        high = bar['high']
        low = bar['low']

        if position['type'] == 'BUY':
            if low <= position['sl']:
                return True, position['sl']
            if high >= position['tp']:
                return True, position['tp']
        else:
            if high >= position['sl']:
                return True, position['sl']
            if low <= position['tp']:
                return True, position['tp']

        return False, None

    # ------------------------------------------------------------------
    # P&L calculation including spread + commission + swap + news widening
    # ------------------------------------------------------------------
    def _calc_pnl(self, position, exit_price, include_costs=True, exit_time=None):
        direction = 1 if position['type'] == 'BUY' else -1
        raw_pnl = (exit_price - position['entry']) * direction * position['size'] * CONTRACT_SIZE

        if include_costs:
            # Spread: widen during news events
            spread_mult = self._news_spread_multiplier(position.get('entry_time'))
            spread_cost = SPREAD_POINTS * spread_mult * position['size'] * CONTRACT_SIZE

            commission = COMMISSION_PER_LOT * position['size']

            # Overnight swap (charged each 22:00 UTC rollover the trade spans)
            swap = self._calc_swap(position, exit_time)

            raw_pnl -= (spread_cost + commission)
            raw_pnl += swap  # swap is negative (cost), so this deducts it

        return raw_pnl

    # ------------------------------------------------------------------
    # Swap cost: count 22:00 UTC rollovers between entry and exit
    # Wednesday rollover = 3× (covers Saturday + Sunday carry)
    # ------------------------------------------------------------------
    def _calc_swap(self, position, exit_time) -> float:
        """Return total swap charge (negative USD). Zero for intraday trades."""
        if exit_time is None or position.get('entry_time') is None:
            return 0.0

        entry_ts = pd.Timestamp(position['entry_time'])
        exit_ts  = pd.Timestamp(exit_time) if not isinstance(exit_time, pd.Timestamp) else exit_time

        swap_rate = SWAP_LONG_PER_LOT if position['type'] == 'BUY' else SWAP_SHORT_PER_LOT
        total_swap = 0.0
        current = entry_ts

        while True:
            # Next 22:00 UTC on or after current
            next_swap = current.normalize() + pd.Timedelta(hours=SWAP_HOUR_UTC)
            if next_swap <= current:
                next_swap += pd.Timedelta(days=1)
            if next_swap >= exit_ts:
                break   # Position closed before the next rollover

            # Wednesday (dayofweek == 2) carries the weekend: counts as 3 days
            nights = 3 if next_swap.dayofweek == 2 else 1
            total_swap += swap_rate * position['size'] * nights
            current = next_swap + pd.Timedelta(minutes=1)

        return total_swap  # negative (cost) or 0

    # ------------------------------------------------------------------
    # Market impact / slippage model for limit order fills
    #
    # Limit orders at 0.5×ATR don't always fill at the posted price:
    #   - Asia session: thin book, price may gap through the level → worse fill
    #   - News events: spread blows out, effective fill 1-3 pts worse
    #   - High-ATR environment: price action is impulsive, partial fills common
    #
    # Slippage is applied to the ENTRY price only (exit SL/TP are fixed levels).
    # Direction: slippage always makes the trade slightly worse (higher buy, lower sell).
    # ------------------------------------------------------------------
    def _apply_entry_slippage(self, price: float, trade_type: str,
                               atr: float, entry_time) -> float:
        """
        Return the realistic fill price after market-impact slippage.
        For BUY: fill_price > limit_price (worse). For SELL: fill_price < limit_price.
        """
        ts = pd.Timestamp(entry_time) if not isinstance(entry_time, pd.Timestamp) else entry_time
        hour = ts.hour
        day  = ts.strftime('%A')
        time_hhmm = ts.strftime('%H:%M')

        # Base slippage by session (in price points)
        if 12 <= hour < 16:          # London / NY overlap — deepest liquidity
            base_slip = 0.10
        elif 8 <= hour < 12:         # London open — decent liquidity
            base_slip = 0.20
        elif 16 <= hour < 20:        # NY afternoon / close
            base_slip = 0.25
        else:                        # Asia — thin book
            base_slip = 0.40

        # News event multiplier
        news_mult = 1.0
        for period in Config.NEWS_BLACKOUT_PERIODS:
            if period['day'] == day and period['start'] <= time_hhmm <= period['end']:
                news_mult = 6.0   # fills can be 1-3 pts worse during FOMC/NFP
                break

        # ATR environment: scale slip proportionally when volatility is high
        # Anchor: ATR of $2 = 2× multiplier, ATR of $0.50 = 0.5× multiplier
        atr_mult = max(0.3, min(3.0, (atr / 1.5))) if atr > 0 else 1.0

        slippage = base_slip * news_mult * atr_mult

        if trade_type == 'BUY':
            return price + slippage
        else:
            return price - slippage

    # ------------------------------------------------------------------
    # News spread multiplier: widen spread during high-impact events
    # ------------------------------------------------------------------
    def _news_spread_multiplier(self, entry_time) -> float:
        """Return spread multiplier. 4× during FOMC, NFP, CPI, etc."""
        if entry_time is None:
            return 1.0

        ts = pd.Timestamp(entry_time) if not isinstance(entry_time, pd.Timestamp) else entry_time
        day_name  = ts.strftime('%A')   # 'Monday', 'Friday', etc.
        time_hhmm = ts.strftime('%H:%M')

        for period in Config.NEWS_BLACKOUT_PERIODS:
            if period['day'] == day_name:
                if period['start'] <= time_hhmm <= period['end']:
                    return NEWS_SPREAD_MULTIPLIER

        return 1.0

    # ------------------------------------------------------------------
    # Session filter by bar timestamp (not wall clock)
    # ------------------------------------------------------------------
    def _bar_in_session(self, ts) -> bool:
        """Return True if the bar's timestamp falls inside the trading window."""
        bar_ts = pd.Timestamp(ts) if not isinstance(ts, pd.Timestamp) else ts
        hour = bar_ts.hour
        # Respect trading day (Mon-Thu) and session hours from Config
        if bar_ts.dayofweek > 3:   # Fri=4, Sat=5, Sun=6
            return False
        if not (Config.TRADING_START_HOUR <= hour < Config.TRADING_END_HOUR):
            return False
        # Session guard: skip first and last SESSION_GUARD_MINUTES
        guard = getattr(Config, 'SESSION_GUARD_MINUTES', 15)
        minutes_into_session = (hour - Config.TRADING_START_HOUR) * 60 + bar_ts.minute
        session_length_mins  = (Config.TRADING_END_HOUR - Config.TRADING_START_HOUR) * 60
        if minutes_into_session < guard:
            return False
        if minutes_into_session > session_length_mins - guard:
            return False
        return True

    # ------------------------------------------------------------------
    # Session classifier
    # ------------------------------------------------------------------
    def _classify_session(self, ts):
        if isinstance(ts, pd.Timestamp):
            hour = ts.hour
        else:
            hour = pd.Timestamp(ts).hour
        if 0 <= hour < 8:
            return 'Asia'
        elif 8 <= hour < 12:
            return 'London_Open'
        elif 12 <= hour < 16:
            return 'London_NY'
        else:
            return 'NY_Close'

    # ------------------------------------------------------------------
    # Full report with all institutional-grade metrics
    # ------------------------------------------------------------------
    def _report(self, initial_balance, final_balance, equity_curve):
        if not self.trades:
            return {'error': 'No trades executed'}

        wins = [t for t in self.trades if t['profit'] > 0]
        losses = [t for t in self.trades if t['profit'] <= 0]
        total = len(self.trades)
        profits = [t['profit'] for t in self.trades]
        gross_profit = sum(t['profit'] for t in wins)
        gross_loss = abs(sum(t['profit'] for t in losses))
        net = final_balance - initial_balance

        # --- Drawdown ---
        eq = np.array(equity_curve)
        running_max = np.maximum.accumulate(eq)
        drawdowns = (eq - running_max) / running_max
        max_drawdown = float(abs(drawdowns.min())) if len(drawdowns) > 0 else 0

        # --- Sharpe ratio (annualised, daily returns) ---
        daily_returns = self._calc_daily_returns(equity_curve)
        sharpe = self._sharpe(daily_returns)
        sortino = self._sortino(daily_returns)
        calmar = (net / initial_balance) / max_drawdown if max_drawdown > 0 else 0

        # --- Performance attribution ---
        by_session = self._attribute(self.trades, 'session')
        by_regime = self._attribute(self.trades, 'regime')
        by_dow = self._attribute_dow(self.trades)

        # --- Monte Carlo (1000 paths via trade shuffling) ---
        mc = self._monte_carlo(profits, initial_balance, n_paths=1000)

        win_rate = len(wins) / total if total > 0 else 0
        avg_win = gross_profit / len(wins) if wins else 0
        avg_loss = gross_loss / len(losses) if losses else 0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        report = {
            'initial_balance': initial_balance,
            'final_balance': round(final_balance, 2),
            'net_profit': round(net, 2),
            'return_pct': round(net / initial_balance * 100, 2),
            'total_trades': total,
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': round(win_rate * 100, 2),
            'profit_factor': round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
            'gross_profit': round(gross_profit, 2),
            'gross_loss': round(gross_loss, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'expectancy': round(expectancy, 2),
            'max_drawdown_pct': round(max_drawdown * 100, 2),
            'sharpe_ratio': round(sharpe, 3),
            'sortino_ratio': round(sortino, 3),
            'calmar_ratio': round(calmar, 3),
            'avg_trade_duration_bars': self._avg_duration(),
            'by_session': by_session,
            'by_regime': by_regime,
            'by_day_of_week': by_dow,
            'monte_carlo': mc,
            'costs_included': True,
            'drawdown_duration': self._drawdown_duration(equity_curve),
            'cost_model': {
                'spread_pts': SPREAD_POINTS,
                'commission_per_lot': COMMISSION_PER_LOT,
                'swap_long_per_lot': SWAP_LONG_PER_LOT,
                'swap_short_per_lot': SWAP_SHORT_PER_LOT,
                'news_spread_mult': NEWS_SPREAD_MULTIPLIER,
            },
        }

        self._print_report(report)
        return report

    def _calc_daily_returns(self, equity_curve):
        eq = np.array(equity_curve)
        if len(eq) < 2:
            return np.array([])
        returns = np.diff(eq) / eq[:-1]
        return returns

    def _sharpe(self, daily_returns, risk_free=0.0):
        if len(daily_returns) < 2:
            return 0.0
        excess = daily_returns - risk_free / 252
        std = np.std(excess)
        if std == 0:
            return 0.0
        return float(np.mean(excess) / std * np.sqrt(252))

    def _sortino(self, daily_returns, risk_free=0.0):
        if len(daily_returns) < 2:
            return 0.0
        excess = daily_returns - risk_free / 252
        downside = excess[excess < 0]
        if len(downside) == 0:
            return float('inf')
        downside_std = np.std(downside)
        if downside_std == 0:
            return 0.0
        return float(np.mean(excess) / downside_std * np.sqrt(252))

    def _avg_duration(self):
        durations = [t['exit_bar'] - t['entry_bar'] for t in self.trades
                     if 'exit_bar' in t and 'entry_bar' in t]
        return round(np.mean(durations), 1) if durations else 0

    def _attribute(self, trades, key):
        groups = {}
        for t in trades:
            k = t.get(key, 'unknown')
            if k not in groups:
                groups[k] = {'trades': 0, 'wins': 0, 'profit': 0.0}
            groups[k]['trades'] += 1
            if t['profit'] > 0:
                groups[k]['wins'] += 1
            groups[k]['profit'] += t['profit']
        for k in groups:
            n = groups[k]['trades']
            groups[k]['win_rate'] = round(groups[k]['wins'] / n * 100, 1) if n > 0 else 0
            groups[k]['profit'] = round(groups[k]['profit'], 2)
        return groups

    def _attribute_dow(self, trades):
        groups = {}
        days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        for t in trades:
            ts = t.get('entry_time')
            if ts is None:
                continue
            if not isinstance(ts, pd.Timestamp):
                ts = pd.Timestamp(ts)
            d = days[ts.dayofweek]
            if d not in groups:
                groups[d] = {'trades': 0, 'wins': 0, 'profit': 0.0}
            groups[d]['trades'] += 1
            if t['profit'] > 0:
                groups[d]['wins'] += 1
            groups[d]['profit'] += t['profit']
        for k in groups:
            n = groups[k]['trades']
            groups[k]['win_rate'] = round(groups[k]['wins'] / n * 100, 1) if n > 0 else 0
            groups[k]['profit'] = round(groups[k]['profit'], 2)
        return groups

    # ------------------------------------------------------------------
    # Monte Carlo simulation via trade-sequence shuffling
    # ------------------------------------------------------------------
    def _monte_carlo(self, profits, initial_balance, n_paths=1000):
        if len(profits) < 5:
            return {}

        arr = np.array(profits)
        final_balances = []
        max_dds = []

        rng = np.random.default_rng(42)
        for _ in range(n_paths):
            shuffled = rng.choice(arr, size=len(arr), replace=True)
            eq = initial_balance + np.cumsum(shuffled)
            eq = np.insert(eq, 0, initial_balance)
            final_balances.append(eq[-1])
            rm = np.maximum.accumulate(eq)
            dd = float(abs(((eq - rm) / rm).min()))
            max_dds.append(dd)

        final_balances = np.array(final_balances)
        max_dds = np.array(max_dds)

        return {
            'median_final': round(float(np.median(final_balances)), 2),
            'p5_final': round(float(np.percentile(final_balances, 5)), 2),
            'p95_final': round(float(np.percentile(final_balances, 95)), 2),
            'ruin_probability': round(float((final_balances < initial_balance * 0.5).mean() * 100), 2),
            'median_max_drawdown_pct': round(float(np.median(max_dds) * 100), 2),
            'worst_drawdown_p95_pct': round(float(np.percentile(max_dds, 95) * 100), 2),
        }

    def _drawdown_duration(self, equity_curve: list) -> dict:
        """Max and average number of bars spent in drawdown (below prior peak)."""
        eq = np.array(equity_curve)
        peak = eq[0]
        in_dd = False
        dd_start = 0
        durations = []

        for idx, val in enumerate(eq):
            if val >= peak:
                if in_dd:
                    durations.append(idx - dd_start)
                    in_dd = False
                peak = val
            else:
                if not in_dd:
                    in_dd = True
                    dd_start = idx

        if in_dd:
            durations.append(len(eq) - dd_start)

        return {
            'max_bars': int(max(durations)) if durations else 0,
            'avg_bars': round(float(np.mean(durations)), 1) if durations else 0,
            'count': len(durations),
        }

    def _print_report(self, r):
        logger.info("=" * 55)
        logger.info("          BACKTEST RESULTS (WITH COSTS)")
        logger.info("=" * 55)
        logger.info(f"  Return       : ${r['net_profit']:>8.2f}  ({r['return_pct']}%)")
        logger.info(f"  Win Rate     : {r['win_rate']}%  ({r['wins']}W / {r['losses']}L)")
        logger.info(f"  Profit Factor: {r['profit_factor']}")
        logger.info(f"  Expectancy   : ${r['expectancy']}/trade")
        logger.info(f"  Max Drawdown : {r['max_drawdown_pct']}%")
        logger.info(f"  Sharpe       : {r['sharpe_ratio']}")
        logger.info(f"  Sortino      : {r['sortino_ratio']}")
        logger.info(f"  Calmar       : {r['calmar_ratio']}")
        logger.info(f"  Avg Duration : {r['avg_trade_duration_bars']} bars")
        dd_dur = r.get('drawdown_duration', {})
        if dd_dur:
            logger.info(
                f"  DD Duration  : max {dd_dur['max_bars']} bars | "
                f"avg {dd_dur['avg_bars']} bars | {dd_dur['count']} drawdown periods"
            )
        logger.info("-" * 55)
        logger.info("  Monte Carlo (1000 paths):")
        mc = r.get('monte_carlo', {})
        if mc:
            logger.info(f"    Median final  : ${mc['median_final']}")
            logger.info(f"    5th pct final : ${mc['p5_final']}")
            logger.info(f"    Ruin prob (<50%): {mc['ruin_probability']}%")
            logger.info(f"    Worst DD (p95): {mc['worst_drawdown_p95_pct']}%")
        logger.info("-" * 55)
        logger.info("  By Session:")
        for s, v in r['by_session'].items():
            logger.info(f"    {s:<14}: {v['trades']} trades | WR {v['win_rate']}% | P&L ${v['profit']}")
        logger.info("=" * 55)


if __name__ == "__main__":
    bt = Backtester()
    results = bt.run(Config.SYMBOL, days=30, initial_balance=Config.SIMULATED_BALANCE)
    if results and 'error' not in results:
        print(f"\nReturn: {results['return_pct']}%")
        print(f"Win Rate: {results['win_rate']}%")
        print(f"Profit Factor: {results['profit_factor']}")
        print(f"Sharpe: {results['sharpe_ratio']}")
        print(f"Max Drawdown: {results['max_drawdown_pct']}%")
        print(f"Ruin Probability: {results['monte_carlo'].get('ruin_probability', 'N/A')}%")
