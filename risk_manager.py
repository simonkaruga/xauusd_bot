from datetime import datetime, date
from config import Config
from logger import logger

class RiskManager:
    def __init__(self):
        self.daily_start_balance = 0
        self.daily_loss = 0
        self.current_date = date.today()
        self.circuit_breaker_active = False
        self.daily_trades = []
        self.daily_profit = 0
        self.trades_today = 0

    def reset_daily_tracking(self, current_balance):
        today = date.today()
        if today != self.current_date:
            self._log_daily_summary()
            self.current_date = today
            self.daily_start_balance = current_balance
            self.daily_loss = 0
            self.daily_profit = 0
            self.circuit_breaker_active = False
            self.daily_trades = []
            self.trades_today = 0
            logger.info(f"Daily tracking reset - Starting balance: ${current_balance:.2f}")

    def _log_daily_summary(self):
        if not self.daily_trades:
            return
        wins = sum(1 for t in self.daily_trades if t['profit'] > 0)
        total = len(self.daily_trades)
        win_rate = (wins / total * 100) if total > 0 else 0
        logger.info(f"=== Daily Summary === Trades: {total} | Win Rate: {win_rate:.1f}% | P&L: ${self.daily_profit:.2f}")

    def update_daily_loss(self, current_balance):
        if self.daily_start_balance == 0:
            self.daily_start_balance = current_balance

        loss = self.daily_start_balance - current_balance
        self.daily_loss = loss / self.daily_start_balance if self.daily_start_balance > 0 else 0

        if self.daily_loss >= Config.MAX_DAILY_LOSS:
            self.circuit_breaker_active = True
            logger.warning(f"CIRCUIT BREAKER ACTIVATED - Daily loss: {self.daily_loss*100:.2f}%")

    def can_trade(self, open_positions_count):
        if self.circuit_breaker_active:
            logger.warning("Trading blocked - Circuit breaker active")
            return False

        if open_positions_count >= Config.MAX_OPEN_TRADES:
            logger.info(f"Max open trades reached: {open_positions_count}")
            return False

        if self.trades_today >= Config.MAX_TRADES_PER_DAY:
            logger.info(f"Max daily trades reached: {self.trades_today}")
            return False

        return True

    def calculate_position_size(self, account_balance, entry_price, sl_price, symbol_info=None):
        """
        Correct XAU/USD position sizing:
        Uses SIMULATED_BALANCE on demo so results match real $1,000 account
        """
        # Use simulated balance on demo to get realistic position sizes
        effective_balance = Config.SIMULATED_BALANCE if hasattr(Config, 'SIMULATED_BALANCE') else account_balance
        risk_amount = effective_balance * Config.RISK_PER_TRADE
        price_diff = abs(entry_price - sl_price)

        if price_diff == 0:
            return 0.01

        contract_size = 100  # XAU/USD = 100 oz per lot
        if symbol_info:
            contract_size = symbol_info.get('trade_contract_size', 100)

        position_size = risk_amount / (price_diff * contract_size)

        # Apply broker volume constraints
        if symbol_info:
            vol_min = symbol_info.get('volume_min', 0.01)
            vol_max = symbol_info.get('volume_max', 100.0)
            vol_step = symbol_info.get('volume_step', 0.01)
            position_size = max(vol_min, min(position_size, vol_max))
            position_size = round(round(position_size / vol_step) * vol_step, 2)
        else:
            position_size = max(0.01, round(position_size, 2))

        logger.info(f"Position size: {position_size} lots | Risk: ${risk_amount:.2f} | SL distance: {price_diff:.2f}")
        return position_size

    def validate_risk_reward(self, entry_price, sl_price, tp_price):
        risk = abs(entry_price - sl_price)
        reward = abs(tp_price - entry_price)

        if risk == 0:
            return False

        rr_ratio = reward / risk

        if rr_ratio < Config.MIN_RISK_REWARD:
            logger.warning(f"R:R too low: {rr_ratio:.2f} (min: {Config.MIN_RISK_REWARD})")
            return False

        logger.info(f"R:R validated: {rr_ratio:.2f}")
        return True

    def track_trade(self, trade_type, entry_price, volume, profit=None):
        self.trades_today += 1
        trade = {
            'time': datetime.now(),
            'type': trade_type,
            'entry': entry_price,
            'volume': volume,
            'profit': profit or 0
        }
        self.daily_trades.append(trade)
        if profit:
            self.daily_profit += profit
