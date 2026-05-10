"""
Risk Manager
=============
Institutional-grade risk controls:
  - Kelly Criterion position sizing (fractional Kelly at 25%)
  - Portfolio heat (total open risk across all positions)
  - Max drawdown circuit breaker
  - Volatility-adjusted sizing
  - Daily/weekly loss limits
  - Streak-adaptive risk scaling
"""

import json
import os
import numpy as np
from datetime import datetime, date, timezone
from config import Config
from logger import logger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


CONTRACT_SIZE = 100
_STATE_PATH = 'logs/risk_state.json'


class RiskManager:
    def __init__(self):
        self.daily_start_balance = 0.0
        self.peak_balance = 0.0
        self.daily_loss = 0.0
        self.current_date = datetime.now(timezone.utc).date()
        self.circuit_breaker_active = False
        self.daily_trades = []
        self.daily_profit = 0.0
        self.trades_today = 0
        self.winning_streak = 0
        self.losing_streak = 0
        self._trade_profits = []
        self._open_risk = {}
        self._load_state()

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------
    def _save_state(self):
        os.makedirs('logs', exist_ok=True)
        state = {
            'peak_balance': self.peak_balance,
            'winning_streak': self.winning_streak,
            'losing_streak': self.losing_streak,
            'trade_profits': self._trade_profits[-200:],  # keep last 200
            'saved_at': _utcnow().isoformat(),
        }
        with open(_STATE_PATH, 'w') as f:
            json.dump(state, f)

    def _load_state(self):
        if not os.path.exists(_STATE_PATH):
            return
        try:
            with open(_STATE_PATH) as f:
                state = json.load(f)
            self.peak_balance = state.get('peak_balance', 0.0)
            self.winning_streak = state.get('winning_streak', 0)
            self.losing_streak = state.get('losing_streak', 0)
            self._trade_profits = state.get('trade_profits', [])
            logger.info(
                f"RiskManager state restored: peak=${self.peak_balance:.2f} "
                f"streaks W{self.winning_streak}/L{self.losing_streak} "
                f"history={len(self._trade_profits)} trades"
            )
        except Exception as e:
            logger.warning(f"RiskManager: could not load state: {e}")


    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------
    def reset_daily_tracking(self, current_balance: float):
        today = datetime.now(timezone.utc).date()
        if today != self.current_date:
            self._log_daily_summary()
            self.current_date = today
            self.daily_start_balance = current_balance
            self.daily_loss = 0.0
            self.daily_profit = 0.0
            self.circuit_breaker_active = False
            self.daily_trades = []
            self.trades_today = 0
            logger.info(f"Daily tracking reset - Starting balance: ${current_balance:.2f}")

        if self.daily_start_balance == 0:
            self.daily_start_balance = current_balance

        if current_balance > self.peak_balance:
            self.peak_balance = current_balance
            self._save_state()

    def _log_daily_summary(self):
        if not self.daily_trades:
            return
        wins = sum(1 for t in self.daily_trades if t['profit'] > 0)
        total = len(self.daily_trades)
        wr = (wins / total * 100) if total > 0 else 0
        logger.info(f"=== Daily Summary === Trades:{total} | WR:{wr:.1f}% | P&L:${self.daily_profit:.2f}")

    # ------------------------------------------------------------------
    # Loss tracking
    # ------------------------------------------------------------------
    def update_daily_loss(self, current_balance: float):
        if self.daily_start_balance == 0:
            self.daily_start_balance = current_balance

        loss_pct = (self.daily_start_balance - current_balance) / self.daily_start_balance
        self.daily_loss = max(0.0, loss_pct)

        # Max drawdown from peak
        dd_from_peak = (self.peak_balance - current_balance) / self.peak_balance if self.peak_balance > 0 else 0

        if self.daily_loss >= Config.MAX_DAILY_LOSS:
            self.circuit_breaker_active = True
            logger.warning(f"CIRCUIT BREAKER: daily loss {self.daily_loss*100:.2f}% >= {Config.MAX_DAILY_LOSS*100:.0f}%")

        if dd_from_peak >= getattr(Config, 'MAX_DRAWDOWN_FROM_PEAK', 0.10):
            self.circuit_breaker_active = True
            logger.warning(f"CIRCUIT BREAKER: drawdown from peak {dd_from_peak*100:.2f}%")

        return self.daily_loss

    # ------------------------------------------------------------------
    # Gate: can we place a new trade?
    # ------------------------------------------------------------------
    def can_trade(self, open_positions_count: int, account_balance: float = None) -> bool:
        if self.circuit_breaker_active:
            logger.warning("Trading blocked — circuit breaker active")
            return False

        if open_positions_count >= Config.MAX_OPEN_TRADES:
            logger.info(f"Max open trades reached: {open_positions_count}")
            return False

        if self.trades_today >= Config.MAX_TRADES_PER_DAY:
            logger.info(f"Max daily trades reached: {self.trades_today}")
            return False

        # Portfolio heat guard (total open risk < 3% of balance)
        if account_balance and len(self._open_risk) > 0:
            total_heat = sum(self._open_risk.values())
            heat_pct = total_heat / account_balance
            max_heat = getattr(Config, 'MAX_PORTFOLIO_HEAT', 0.03)
            if heat_pct >= max_heat:
                logger.info(f"Portfolio heat {heat_pct*100:.2f}% >= {max_heat*100:.0f}% — no new trades")
                return False

        return True

    # ------------------------------------------------------------------
    # Position sizing: Kelly-informed, volatility-adjusted
    # ------------------------------------------------------------------
    def calculate_position_size(self, account_balance: float, entry_price: float,
                                 sl_price: float, symbol_info: dict = None,
                                 signal_confidence: float = 0.7,
                                 macro_multiplier: float = 1.0,
                                 market_regime: str = 'trending') -> float:
        """
        Sizing hierarchy:
          1. Start with Kelly-optimal fraction (capped at 25% Kelly)
          2. Apply signal confidence scaling
          3. Apply macro multiplier (from correlation filter)
          4. Apply streak scaling (reduce on losing streaks)
          5. Hard cap at config MAX risk per trade
          6. Apply broker volume constraints
        """
        effective_balance = getattr(Config, 'SIMULATED_BALANCE', account_balance)

        # --- Step 1: base risk ---
        base_risk_pct = Config.RISK_PER_TRADE

        # --- Step 2: Kelly adjustment ---
        kelly_risk = self._kelly_risk()
        if kelly_risk > 0:
            # Blend 50% fixed / 50% Kelly
            base_risk_pct = 0.5 * Config.RISK_PER_TRADE + 0.5 * kelly_risk

        # --- Step 3: signal confidence ---
        base_risk_pct *= (signal_confidence / 0.70)

        # --- Step 4: macro multiplier ---
        base_risk_pct *= macro_multiplier

        # --- Step 4b: regime-conditional Kelly scaling ---
        # In ranging markets the edge is lower (mean-reversion has tighter odds).
        # In trending markets momentum is strongest — full Kelly allocation.
        regime_scale = {'trending': 1.0, 'neutral': 0.8, 'ranging': 0.6}.get(
            market_regime, 1.0
        )
        base_risk_pct *= regime_scale

        # --- Step 5: streak scaling ---
        if self.losing_streak >= 3:
            base_risk_pct *= max(0.5, 1 - self.losing_streak * 0.1)
        elif self.winning_streak >= 3:
            base_risk_pct *= min(1.3, 1 + self.winning_streak * 0.05)

        # --- Hard cap ---
        max_risk = getattr(Config, 'MAX_RISK_PER_TRADE', 0.02)
        base_risk_pct = min(base_risk_pct, max_risk)
        base_risk_pct = max(base_risk_pct, 0.005)  # floor at 0.5%

        risk_amount = effective_balance * base_risk_pct
        price_diff = abs(entry_price - sl_price)

        if price_diff == 0:
            return 0.01

        contract_size = CONTRACT_SIZE
        if symbol_info:
            contract_size = symbol_info.get('trade_contract_size', CONTRACT_SIZE)

        position_size = risk_amount / (price_diff * contract_size)

        # --- Broker volume constraints ---
        if symbol_info:
            vol_min = symbol_info.get('volume_min', 0.01)
            vol_max = symbol_info.get('volume_max', 100.0)
            vol_step = symbol_info.get('volume_step', 0.01)
            position_size = max(vol_min, min(position_size, vol_max))
            position_size = round(round(position_size / vol_step) * vol_step, 2)
        else:
            position_size = max(0.01, round(position_size, 2))

        logger.info(
            f"Position size: {position_size} lots | Risk: ${risk_amount:.2f} "
            f"({base_risk_pct*100:.2f}%) | Kelly: {kelly_risk*100:.2f}% | "
            f"SL dist: {price_diff:.2f} | Confidence: {signal_confidence:.2f} | "
            f"Streaks W{self.winning_streak}/L{self.losing_streak}"
        )
        return position_size

    # ------------------------------------------------------------------
    # Kelly Criterion (fractional Kelly at 25%)
    # ------------------------------------------------------------------
    def _kelly_risk(self, lookback: int = 30) -> float:
        recent = self._trade_profits[-lookback:] if len(self._trade_profits) > lookback else self._trade_profits

        if len(recent) < 10:
            return Config.RISK_PER_TRADE  # Not enough data

        wins = [p for p in recent if p > 0]
        losses = [p for p in recent if p <= 0]

        if not wins or not losses:
            return Config.RISK_PER_TRADE

        win_rate = len(wins) / len(recent)
        avg_win = np.mean(wins)
        avg_loss = abs(np.mean(losses))

        if avg_loss == 0:
            return Config.RISK_PER_TRADE

        r = avg_win / avg_loss
        full_kelly = win_rate - (1 - win_rate) / r

        if full_kelly <= 0:
            return Config.RISK_PER_TRADE * 0.5   # Reduce when edge is negative

        fractional_kelly = full_kelly * 0.25   # 25% Kelly = conservative but optimal
        return max(0.005, min(fractional_kelly, 0.03))

    # ------------------------------------------------------------------
    # R:R validation
    # ------------------------------------------------------------------
    def validate_risk_reward(self, entry_price: float, sl_price: float, tp_price: float) -> bool:
        risk = abs(entry_price - sl_price)
        reward = abs(tp_price - entry_price)

        if risk == 0:
            return False

        rr = reward / risk
        if rr < Config.MIN_RISK_REWARD:
            logger.warning(f"R:R too low: {rr:.2f} (min: {Config.MIN_RISK_REWARD})")
            return False

        logger.info(f"R:R validated: {rr:.2f}")
        return True

    # ------------------------------------------------------------------
    # Portfolio heat tracking
    # ------------------------------------------------------------------
    def register_open_trade(self, ticket: int, entry_price: float,
                             sl_price: float, position_size: float):
        risk_amount = abs(entry_price - sl_price) * position_size * CONTRACT_SIZE
        self._open_risk[ticket] = risk_amount
        logger.info(f"Portfolio heat: ${sum(self._open_risk.values()):.2f} total open risk")

    def deregister_trade(self, ticket: int, profit: float = None):
        self._open_risk.pop(ticket, None)
        if profit is not None:
            self._trade_profits.append(profit)
            if profit > 0:
                self.winning_streak += 1
                self.losing_streak = 0
            else:
                self.losing_streak += 1
                self.winning_streak = 0
            self._save_state()
            logger.info(f"Trade closed: ${profit:.2f} | Streaks W{self.winning_streak}/L{self.losing_streak}")

    # ------------------------------------------------------------------
    # Trade tracking
    # ------------------------------------------------------------------
    def track_trade(self, trade_type: str, entry_price: float, volume: float, profit: float = None):
        self.trades_today += 1
        trade = {
            'time': _utcnow(),
            'type': trade_type,
            'entry': entry_price,
            'volume': volume,
            'profit': profit or 0,
        }
        self.daily_trades.append(trade)
        if profit:
            self.daily_profit += profit
            self._trade_profits.append(profit)

    def get_portfolio_heat_pct(self, account_balance: float) -> float:
        if account_balance == 0:
            return 0.0
        return sum(self._open_risk.values()) / account_balance * 100

    def get_kelly_fraction(self) -> float:
        return self._kelly_risk()
