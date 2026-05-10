"""
Trading Bot Orchestrator
=========================
Wires all modules together for a single trading cycle:
  - AdvancedStrategy (regime-adaptive: trend / reversal / breakout)
  - MLSignalClassifier (quality gate)
  - CorrelationFilter (macro USD alignment)
  - RiskManager (Kelly sizing, portfolio heat, drawdown guard)
  - TradeDatabase (full attribution: session, regime, confidence, ML score)
  - TelegramNotifier, HealthMonitor, KillSwitch
"""

import MetaTrader5 as mt5
import pandas_ta as ta
from datetime import datetime, timedelta, timezone
from mt5_connector import MT5Connector
from advanced_strategy import AdvancedStrategy
from risk_manager import RiskManager
from position_monitor import PositionMonitor
from kill_switch import KillSwitch
from execution_tracker import ExecutionTracker
from health_monitor import HealthMonitor
from news_monitor import NewsMonitor
from news_filter import NewsFilter
from volatility_manager import VolatilityManager
from telegram_notifier import TelegramNotifier
from trade_database import TradeDatabase
from ml_signal_classifier import MLSignalClassifier
from correlation_filter import CorrelationFilter
from cot_fetcher import COTFetcher
from dom_analysis import DOMAnalysis
from vwap_filter import VWAPFilter
from macro_engine import MacroEngine
from news_sentiment import NewsSentiment
from config import Config
from logger import logger


COMMISSION_PER_LOT = 7.0
SPREAD_POINTS = 0.35
CONTRACT_SIZE = 100


def _classify_session(hour: int) -> str:
    if 0 <= hour < 8:
        return 'Asia'
    elif 8 <= hour < 12:
        return 'London_Open'
    elif 12 <= hour < 16:
        return 'London_NY'
    return 'NY_Close'


class TradingBot:
    def __init__(self):
        self.connector = MT5Connector()
        self.strategy = AdvancedStrategy()
        self.risk_manager = RiskManager()
        self.position_monitor = PositionMonitor(self.connector)
        self.kill_switch = KillSwitch()
        self.execution_tracker = ExecutionTracker()
        self.health_monitor = HealthMonitor()
        self.news_monitor = NewsMonitor()
        self.news_filter = NewsFilter()
        self.volatility_manager = VolatilityManager()
        self.notifier = TelegramNotifier()
        self.db = TradeDatabase()
        self.ml_classifier = MLSignalClassifier()
        self.correlation_filter = CorrelationFilter(connector=self.connector)
        self.cot_fetcher = COTFetcher()
        self.dom = DOMAnalysis()
        self.vwap_filter = VWAPFilter()
        self.macro_engine = MacroEngine(connector=self.connector)
        self.news_sentiment = NewsSentiment()
        self.running = False

        # Pending limit order tracking: {ticket: {signal, expires_at, size}}
        self._pending_limits: dict = {}
        self._cot_last_refresh = None

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------
    def start(self) -> bool:
        if not self.connector.connect():
            logger.error("Failed to connect to MT5")
            return False

        account_info = self.connector.get_account_info()
        if account_info:
            self.risk_manager.reset_daily_tracking(account_info['balance'])
            logger.info(
                f"Bot started | Balance: ${account_info['balance']:.2f} | "
                f"Server: {Config.MT5_SERVER}"
            )

        self.dom.enable(Config.SYMBOL)
        self._restore_open_positions()
        self.running = True
        self.health_monitor.heartbeat()
        return True

    def _restore_open_positions(self):
        """
        On startup, re-register any positions that were open before a crash.
        Without this, position_monitor and risk_manager are blind to existing
        trades and trailing stops / portfolio heat won't work correctly.
        """
        open_db = self.db.get_open_trades()
        live_positions = {p.ticket: p for p in self.connector.get_positions(Config.SYMBOL)}

        restored = 0
        for row in open_db:
            # row columns: id(0) ts(1) ticket(2) symbol(3) type(4) entry(5)
            # exit(6) sl(7) tp(8) vol(9) profit(10) ... status(14) ... atr(?) ml(19)
            ticket = row[2]
            if ticket not in live_positions:
                # Position closed while bot was offline — reconcile now
                deals = self.connector.get_history_deals(days=7)
                for deal in deals:
                    if getattr(deal, 'position_id', None) == ticket:
                        profit = getattr(deal, 'profit', 0)
                        price = getattr(deal, 'price', 0)
                        commission = abs(getattr(deal, 'commission', 0))
                        swap = getattr(deal, 'swap', 0)
                        self.db.close_trade(ticket, price, profit + swap, commission=commission)
                        logger.info(f"Restored closed trade: ticket={ticket} P&L=${profit + swap:.2f}")
                        break
                continue

            pos = live_positions[ticket]
            entry = row[5]
            sl = row[7]
            volume = row[9]
            # Use a default ATR of 2.0 if not stored; trailing stop will recalculate
            atr = 2.0
            self.position_monitor.track(ticket, atr)
            self.risk_manager.register_open_trade(ticket, entry, sl, volume)
            restored += 1
            logger.info(f"Restored open position: ticket={ticket} entry={entry} vol={volume}")

        if restored:
            logger.info(f"Startup restore: {restored} open position(s) re-registered")

    def stop(self):
        self.running = False
        self.connector.disconnect()
        logger.info("Bot stopped")

    # ------------------------------------------------------------------
    # Main cycle (called every 5 min by scheduler)
    # ------------------------------------------------------------------
    def execute_trading_cycle(self):
        if not self._pre_cycle_checks():
            return
        try:
            self._maybe_refresh_cot()
            self._manage_pending_limits()

            if not self.news_filter.is_safe_to_trade() or not self.news_monitor.is_safe_to_trade():
                return

            balance, positions = self._update_account_state()
            if balance is None:
                return

            if not self.risk_manager.can_trade(len(positions), balance):
                return

            signal, ml_score, macro_mult, macro_reason = self._evaluate_signal(balance)
            if signal is None:
                return

            position_size = self._calculate_size(signal, balance, macro_mult)
            self._close_opposite_positions(positions, signal['type'])
            self._place_order(signal, position_size, ml_score, macro_mult, macro_reason)

        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
            self.health_monitor.record_error(str(e))

    def _pre_cycle_checks(self) -> bool:
        if self.kill_switch.is_active():
            self.stop()
            return False
        if self.kill_switch.is_paused() or not self.running:
            return False
        self.health_monitor.heartbeat()
        if not self.health_monitor.check_health():
            if not self.health_monitor.auto_recover():
                self.kill_switch.activate("Health check failed")
            return False
        return True

    def _update_account_state(self):
        """Refresh balance, trailing stops, reconcile. Returns (balance, positions) or (None, None)."""
        account_info = self.connector.get_account_info()
        if not account_info:
            self.health_monitor.record_error("Account info failed")
            return None, None

        balance = account_info['balance']
        self.risk_manager.reset_daily_tracking(balance)
        self.risk_manager.update_daily_loss(balance)
        self.position_monitor.update_trailing_stops()
        self._reconcile_closed_positions()

        positions = self.connector.get_positions(Config.SYMBOL)
        logger.info(
            f"Balance: ${balance:.2f} | Equity: ${account_info['equity']:.2f} | "
            f"Open: {len(positions)} | Daily Loss: {self.risk_manager.daily_loss*100:.2f}% | "
            f"Portfolio Heat: {self.risk_manager.get_portfolio_heat_pct(balance):.1f}%"
        )
        return balance, positions

    def _evaluate_signal(self, balance: float):
        """Fetch data, run all signal gates. Returns (signal, ml_score, macro_mult, macro_reason) or (None,…)."""
        _none = (None, None, None, None)

        df = self.connector.get_bars(Config.SYMBOL, Config.TIMEFRAME, 200)
        if df is None or len(df) < 55:
            self.health_monitor.record_error("Market data failed")
            return _none

        df_h1 = df_h4 = None
        if Config.USE_MTF_CONFIRMATION:
            df_h1 = self.connector.get_bars(Config.SYMBOL, Config.HIGHER_TIMEFRAME, 100)
            df_h4 = self.connector.get_bars(Config.SYMBOL, Config.H4_TIMEFRAME, 60)

        macro = self.macro_engine.get_macro_score()
        sentiment = self.news_sentiment.get_sentiment_score()
        self.strategy.set_macro_context(
            macro_score=macro['score'], macro_direction=macro['direction'],
            cot_score=macro['cot_score'], sentiment_score=sentiment['score'],
        )

        vwap_data = self.vwap_filter.compute(df)
        vwap_price = vwap_data.get('vwap') if vwap_data else None
        signal = self.strategy.generate_signal(df, df_h1, df_h4, vwap=vwap_price)
        if signal is None:
            return _none

        ml_score = self.ml_classifier.predict(df, signal['type'])
        if not self.ml_classifier.should_trade(df, signal['type']):
            logger.info(f"ML gate BLOCKED signal (score={ml_score:.3f})")
            return _none

        macro_ok, macro_mult, macro_reason = self.correlation_filter.check(signal['type'], df)
        if not macro_ok:
            return _none

        dom_ok, dom_mult = self.dom.check_signal(signal['type'], Config.SYMBOL)
        if not dom_ok:
            logger.info(f"DOM gate BLOCKED {signal['type']}")
            return _none

        _, vwap_mult = self.vwap_filter.check_signal(signal['type'], df)
        macro_mult *= dom_mult * vwap_mult * self.news_sentiment.get_size_multiplier(signal['type'])

        if self._near_round_number(signal['price']):
            logger.info(f"ROUND NUMBER GUARD: skipping {signal['type']} @ {signal['price']:.2f}")
            return _none

        if not self.risk_manager.validate_risk_reward(signal['price'], signal['sl'], signal['tp']):
            return _none

        return signal, ml_score, macro_mult, macro_reason

    def _calculate_size(self, signal: dict, balance: float, macro_mult: float) -> float:
        """Compute volatility-adjusted position size."""
        symbol_info = self.connector.get_symbol_info(Config.SYMBOL)
        avg_atr = signal['atr']
        try:
            df = self.connector.get_bars(Config.SYMBOL, Config.TIMEFRAME, 200)
            s = ta.atr(df['high'], df['low'], df['close'], length=Config.ATR_PERIOD)
            if s is not None and not s.dropna().empty:
                avg_atr = float(s.dropna().mean())
        except Exception:
            pass
        vol_adj = self.volatility_manager.adjust_risk_for_volatility(signal['atr'], avg_atr)

        size = self.risk_manager.calculate_position_size(
            balance, signal['price'], signal['sl'],
            symbol_info=symbol_info,
            signal_confidence=signal.get('confidence', 0.7),
            macro_multiplier=macro_mult,
            market_regime=signal.get('regime', 'trending'),
        )
        size = max(0.01, round(size * (vol_adj / Config.RISK_PER_TRADE), 2))
        if symbol_info:
            vol_min = symbol_info.get('volume_min', 0.01)
            vol_step = symbol_info.get('volume_step', 0.01)
            size = max(vol_min, round(round(size / vol_step) * vol_step, 2))
        return size

    def _close_opposite_positions(self, positions, signal_type: str):
        for pos in positions:
            if (signal_type == 'BUY' and pos.type == mt5.ORDER_TYPE_SELL) or \
               (signal_type == 'SELL' and pos.type == mt5.ORDER_TYPE_BUY):
                if self.connector.close_position(pos.ticket):
                    self.position_monitor.untrack(pos.ticket)
                    self.risk_manager.deregister_trade(pos.ticket)
                    logger.info(f"Closed opposite position: {pos.ticket}")

    def _place_order(self, signal, position_size, ml_score, macro_mult, macro_reason):
        tick = mt5.symbol_info_tick(Config.SYMBOL)
        if tick is None:
            self.health_monitor.record_error("No tick data")
            return
        session = _classify_session(datetime.now(timezone.utc).hour)
        use_limit = getattr(Config, 'USE_LIMIT_ORDERS', True)
        atr = signal.get('atr', 0)
        offset = atr * getattr(Config, 'ATR_LIMIT_OFFSET', 0.5)
        expiry_seconds = getattr(Config, 'LIMIT_ORDER_EXPIRY_BARS', 1) * 15 * 60

        if use_limit and offset > 0:
            self._try_limit_order(signal, position_size, tick, session,
                                   ml_score, macro_mult, macro_reason, offset, expiry_seconds)
        else:
            self._place_market_order(signal, position_size, tick, session,
                                     ml_score, macro_mult, macro_reason)

    def _try_limit_order(self, signal, position_size, tick, session,
                          ml_score, macro_mult, macro_reason, offset, expiry_seconds):
        if signal['type'] == 'BUY':
            limit_price, limit_type, intended_price = tick.ask - offset, mt5.ORDER_TYPE_BUY_LIMIT, tick.ask
        else:
            limit_price, limit_type, intended_price = tick.bid + offset, mt5.ORDER_TYPE_SELL_LIMIT, tick.bid

        ticket = self.connector.place_limit_order(
            Config.SYMBOL, limit_type, position_size,
            limit_price=limit_price, sl=signal['sl'], tp=signal['tp'],
            comment=f"{self.strategy.name}|{signal.get('regime','?')}",
            expiry_seconds=expiry_seconds,
        )
        if ticket:
            self._pending_limits[ticket] = {
                'signal': signal, 'size': position_size, 'session': session,
                'ml_score': ml_score, 'macro_mult': macro_mult, 'macro_reason': macro_reason,
                'intended_price': intended_price, 'limit_price': limit_price,
                'expires_at': datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=expiry_seconds),
            }
            logger.info(
                f"LIMIT ORDER: {signal['type']} {position_size}L @ {limit_price:.2f} "
                f"(offset {offset:.2f} from {intended_price:.2f}) | expires in {expiry_seconds}s | ticket={ticket}"
            )
            self.health_monitor.reset_errors()
        else:
            logger.warning("Limit order failed — falling back to market order")
            self._place_market_order(signal, position_size, tick, session,
                                     ml_score, macro_mult, macro_reason)

    # ------------------------------------------------------------------
    # Market order placement (shared by direct + limit-fallback paths)
    # ------------------------------------------------------------------
    def _place_market_order(self, signal, position_size, tick, session,
                             ml_score, macro_mult, macro_reason):
        order_type = mt5.ORDER_TYPE_BUY if signal['type'] == 'BUY' else mt5.ORDER_TYPE_SELL
        intended_price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        result = self.connector.place_order(
            Config.SYMBOL, order_type, position_size,
            sl=signal['sl'], tp=signal['tp'],
            comment=f"{self.strategy.name}|{signal.get('regime','?')}",
        )

        if result:
            executed_price = result.price
            self.execution_tracker.track_execution(intended_price, executed_price, signal['type'])
            self.position_monitor.track(result.order, signal['atr'])
            self.risk_manager.track_trade(signal['type'], executed_price, position_size)
            self.risk_manager.register_open_trade(
                result.order, executed_price, signal['sl'], position_size
            )
            self.db.insert_trade(
                ticket=result.order, symbol=Config.SYMBOL,
                trade_type=signal['type'], entry=executed_price,
                sl=signal['sl'], tp=signal['tp'], volume=position_size,
                session=session, regime=signal.get('regime', 'unknown'),
                confidence=signal.get('confidence', 0.0),
                macro_mult=macro_mult, ml_score=ml_score, comment=macro_reason,
            )
            rr = abs(signal['tp'] - executed_price) / max(abs(executed_price - signal['sl']), 0.01)
            self.notifier.notify_trade(
                signal['type'], Config.SYMBOL, executed_price,
                signal['sl'], signal['tp'], position_size,
            )
            self.health_monitor.reset_errors()
            logger.info(
                f"MARKET TRADE: {signal['type']} {position_size}L @ {executed_price:.2f} | "
                f"SL:{signal['sl']:.2f} TP:{signal['tp']:.2f} R:R:{rr:.2f} | "
                f"Regime:{signal.get('regime','?')} Conf:{signal.get('confidence',0):.2f} "
                f"ML:{ml_score:.3f} Macro:{macro_mult:.2f}x"
            )
        else:
            self.health_monitor.record_error("Order execution failed")
            self.notifier.notify_error("Order execution failed")

    # ------------------------------------------------------------------
    # Manage pending limit orders: detect fills, cancel expired
    # ------------------------------------------------------------------
    def _manage_pending_limits(self):
        if not self._pending_limits:
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        live_pending = {t.order for t in self.connector.get_pending_orders(Config.SYMBOL)}
        live_positions = {p.ticket for p in self.connector.get_positions(Config.SYMBOL)}

        to_remove = []
        for ticket, meta in self._pending_limits.items():
            signal = meta['signal']

            # Check if it became a position (filled)
            if ticket in live_positions or ticket not in live_pending:
                position = next(
                    (p for p in self.connector.get_positions(Config.SYMBOL)
                     if p.ticket == ticket), None
                )
                if position:
                    executed_price = position.price_open
                    size = meta['size']
                    self.execution_tracker.track_execution(
                        meta['intended_price'], executed_price, signal['type']
                    )
                    self.position_monitor.track(ticket, signal['atr'])
                    self.risk_manager.track_trade(signal['type'], executed_price, size)
                    self.risk_manager.register_open_trade(
                        ticket, executed_price, signal['sl'], size
                    )
                    self.db.insert_trade(
                        ticket=ticket, symbol=Config.SYMBOL,
                        trade_type=signal['type'], entry=executed_price,
                        sl=signal['sl'], tp=signal['tp'], volume=size,
                        session=meta['session'], regime=signal.get('regime', 'unknown'),
                        confidence=signal.get('confidence', 0.0),
                        macro_mult=meta['macro_mult'], ml_score=meta['ml_score'],
                        comment=f"LIMIT_FILL|{meta['macro_reason']}",
                    )
                    slippage_vs_limit = abs(executed_price - meta['limit_price'])
                    logger.info(
                        f"LIMIT FILLED: ticket={ticket} @ {executed_price:.2f} "
                        f"(limit was {meta['limit_price']:.2f}, "
                        f"saved {abs(meta['intended_price'] - executed_price):.2f} vs market)"
                    )
                    self.notifier.notify_trade(
                        signal['type'], Config.SYMBOL, executed_price,
                        signal['sl'], signal['tp'], size,
                    )
                    to_remove.append(ticket)

                elif ticket not in live_pending:
                    # Order disappeared without filling (expired or cancelled externally)
                    logger.info(f"Limit order {ticket} expired/cancelled unfilled")
                    to_remove.append(ticket)

            # Cancel if past our expiry time (safety net — MT5 expiry should handle this)
            elif now > meta['expires_at']:
                logger.info(f"Cancelling stale limit order {ticket}")
                self.connector.cancel_order(ticket)
                to_remove.append(ticket)

        for t in to_remove:
            self._pending_limits.pop(t, None)

    # ------------------------------------------------------------------
    # Round-number proximity guard
    # XAU/USD stops cluster at $50 and $100 round numbers (e.g. $2000,
    # $2050, $2100). Entering within BUFFER pts of these levels:
    #   - Triggers institutional stop hunts that spike price through the level
    #   - Gives an artificially bad fill if using a limit order
    #   - Increases the chance SL is hit on noise before direction plays out
    # ------------------------------------------------------------------
    ROUND_NUMBER_BUFFER = 3.0    # pts — skip if within $3 of a $50 boundary

    def _near_round_number(self, price: float) -> bool:
        """Return True if price is within ROUND_NUMBER_BUFFER of a $50 level."""
        nearest_50 = round(price / 50) * 50
        return abs(price - nearest_50) <= self.ROUND_NUMBER_BUFFER

    # ------------------------------------------------------------------
    # Weekly COT refresh
    # ------------------------------------------------------------------
    def _maybe_refresh_cot(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Refresh every Friday after 21:00 UTC (CFTC releases ~20:30 UTC)
        is_friday = now.weekday() == 4
        past_release = now.hour >= 21
        already_done_today = (
            self._cot_last_refresh is not None and
            self._cot_last_refresh.date() == now.date()
        )
        if is_friday and past_release and not already_done_today:
            try:
                report = self.cot_fetcher.get_full_report()
                cot_score = report.get('cot_score', 0.0)
                self.ml_classifier.set_cot_score(cot_score)
                self.macro_engine.set_cot_score(cot_score)
                self._cot_last_refresh = now
                logger.info(
                    f"COT weekly refresh: score={cot_score:+.3f} | "
                    f"net={report.get('cot_net',0):+,} | "
                    f"change={report.get('cot_change_week',0):+,}"
                )
            except Exception as e:
                logger.warning(f"COT refresh failed: {e}")

    # ------------------------------------------------------------------
    # Reconcile: detect broker-closed positions and update DB
    # ------------------------------------------------------------------
    def _reconcile_closed_positions(self):
        """Check history deals to close any positions that hit SL/TP on broker side."""
        try:
            deals = self.connector.get_history_deals(days=1)
            tracked_tickets = set(self.position_monitor.tracked.keys())

            for deal in deals:
                ticket = getattr(deal, 'position_id', None)
                if ticket and ticket in tracked_tickets:
                    profit = getattr(deal, 'profit', 0)
                    price = getattr(deal, 'price', 0)
                    commission = abs(getattr(deal, 'commission', 0))
                    swap = getattr(deal, 'swap', 0)
                    net_profit = profit + swap

                    self.db.close_trade(ticket, price, net_profit, commission=commission)
                    self.risk_manager.deregister_trade(ticket, profit=net_profit)
                    self.position_monitor.untrack(ticket)

                    if net_profit > 0:
                        self.notifier.send_message(
                            f"✅ TP HIT | Ticket:{ticket} | P&L: +${net_profit:.2f}"
                        )
                    else:
                        self.notifier.send_message(
                            f"❌ SL HIT | Ticket:{ticket} | P&L: ${net_profit:.2f}"
                        )
        except Exception as e:
            logger.warning(f"Reconcile error: {e}")

    def run_once(self):
        self.execute_trading_cycle()
