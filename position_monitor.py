"""
Position Monitor
=================
Manages open positions after entry:
  - Trailing stop (ATR-based, activates after breakeven trigger)
  - Breakeven move (SL → entry + buffer when profit ≥ 1.5×ATR)
  - TP1/TP2 partial scaling (close 50% at 2R, let 50% run to 4R)
  - Time stop (close after 8 bars of no resolution — no dead capital)
"""

import MetaTrader5 as mt5
from datetime import datetime, timezone
from config import Config
from logger import logger


class PositionMonitor:
    def __init__(self, connector):
        self.connector = connector
        # ticket → {atr, entry_time, partial_done, bars_open}
        self.tracked: dict = {}

    def track(self, ticket: int, atr: float):
        self.tracked[ticket] = {
            'atr': atr,
            'entry_time': datetime.now(tz=timezone.utc),
            'partial_done': False,   # TP1 partial close already executed
            'bars_open': 0,
        }

    def untrack(self, ticket: int):
        self.tracked.pop(ticket, None)

    # ------------------------------------------------------------------
    # Called every cycle
    # ------------------------------------------------------------------
    def update_trailing_stops(self):
        if not Config.ENABLE_TRAILING_STOP:
            return
        for pos in self.connector.get_positions(Config.SYMBOL):
            meta = self.tracked.get(pos.ticket)
            if meta:
                self._update_position(pos, meta)

    def _update_position(self, pos, meta: dict):
        meta['bars_open'] += 1
        atr = meta['atr']
        entry = pos.price_open
        current = pos.price_current
        sl = pos.sl
        is_buy = (pos.type == mt5.ORDER_TYPE_BUY)
        profit_pts = (current - entry) if is_buy else (entry - current)

        if Config.USE_PARTIAL_CLOSE and not meta['partial_done']:
            self._maybe_partial_close(pos, meta, atr, profit_pts, current)

        if Config.USE_TIME_STOP and self._should_time_stop(pos.ticket, meta, atr, profit_pts):
            return

        self._update_sl(pos.ticket, is_buy, entry, current, sl, atr, profit_pts)

    def _maybe_partial_close(self, pos, meta: dict, atr: float,
                              profit_pts: float, current: float):
        if profit_pts >= atr * Config.ATR_MULTIPLIER_TP1:
            close_vol = max(round(pos.volume * Config.TP1_FRACTION, 2),
                            self._get_vol_min(pos.symbol))
            if close_vol < pos.volume and self._partial_close(pos, close_vol):
                meta['partial_done'] = True
                logger.info(
                    f"TP1 partial close: ticket={pos.ticket} "
                    f"closed {close_vol}L of {pos.volume}L @ {current:.2f} (+{profit_pts:.2f}pts)"
                )

    def _should_time_stop(self, ticket: int, meta: dict, atr: float, profit_pts: float) -> bool:
        if meta['bars_open'] >= Config.TIME_STOP_BARS and profit_pts < atr * 0.5:
            logger.info(
                f"TIME STOP: ticket={ticket} open {meta['bars_open']} bars "
                f"with only {profit_pts:.2f}pts profit — closing"
            )
            self.connector.close_position(ticket)
            self.untrack(ticket)
            return True
        return False

    def _update_sl(self, ticket: int, is_buy: bool, entry: float,
                    current: float, sl: float, atr: float, profit_pts: float):
        be_trigger = atr * Config.BREAKEVEN_TRIGGER
        trail_dist = atr * Config.TRAILING_DISTANCE
        if is_buy:
            if profit_pts >= be_trigger and sl < entry:
                new_sl = entry + atr * 0.1
                if self.connector.modify_position_sl(ticket, new_sl):
                    logger.info(f"Breakeven set: {ticket} SL→{new_sl:.2f}")
            elif profit_pts > be_trigger:
                new_sl = current - trail_dist
                if new_sl > sl and self.connector.modify_position_sl(ticket, new_sl):
                    logger.info(f"Trail SL: {ticket}→{new_sl:.2f}")
        else:
            if profit_pts >= be_trigger and sl > entry:
                new_sl = entry - atr * 0.1
                if self.connector.modify_position_sl(ticket, new_sl):
                    logger.info(f"Breakeven set: {ticket} SL→{new_sl:.2f}")
            elif profit_pts > be_trigger:
                new_sl = current + trail_dist
                if new_sl < sl and self.connector.modify_position_sl(ticket, new_sl):
                    logger.info(f"Trail SL: {ticket}→{new_sl:.2f}")

    # ------------------------------------------------------------------
    # Partial close helper
    # ------------------------------------------------------------------
    def _partial_close(self, pos, volume: float) -> bool:
        """Close `volume` lots of an open position."""
        order_type = (mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY
                      else mt5.ORDER_TYPE_BUY)
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            return False
        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        for fill in [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]:
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": volume,
                "type": order_type,
                "position": pos.ticket,
                "price": price,
                "deviation": 20,
                "magic": 234000,
                "comment": "TP1_partial",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": fill,
            }
            result = mt5.order_send(req)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                return True
            if result and result.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                logger.warning(f"Partial close failed: {result.retcode} {result.comment}")
                return False
        return False

    def _get_vol_min(self, symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        return info.volume_min if info else 0.01
