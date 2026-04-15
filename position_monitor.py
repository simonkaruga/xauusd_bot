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

        positions = self.connector.get_positions(Config.SYMBOL)

        for pos in positions:
            ticket = pos.ticket
            meta = self.tracked.get(ticket)
            if not meta:
                continue

            meta['bars_open'] += 1
            atr = meta['atr']
            entry = pos.price_open
            current = pos.price_current
            sl = pos.sl

            is_buy = (pos.type == mt5.ORDER_TYPE_BUY)
            profit_pts = (current - entry) if is_buy else (entry - current)

            # --------------------------------------------------------
            # 1. Partial close at TP1 (first time only)
            # --------------------------------------------------------
            if Config.USE_PARTIAL_CLOSE and not meta['partial_done']:
                tp1_dist = atr * Config.ATR_MULTIPLIER_TP1
                if profit_pts >= tp1_dist:
                    close_vol = round(pos.volume * Config.TP1_FRACTION, 2)
                    close_vol = max(close_vol,
                                    self._get_vol_min(pos.symbol))
                    if close_vol < pos.volume:
                        ok = self._partial_close(pos, close_vol)
                        if ok:
                            meta['partial_done'] = True
                            logger.info(
                                f"TP1 partial close: ticket={ticket} "
                                f"closed {close_vol}L of {pos.volume}L "
                                f"@ {current:.2f} (+{profit_pts:.2f}pts)"
                            )

            # --------------------------------------------------------
            # 2. Time stop — close if unresolved after N bars
            # --------------------------------------------------------
            if Config.USE_TIME_STOP:
                max_bars = Config.TIME_STOP_BARS
                if meta['bars_open'] >= max_bars and profit_pts < atr * 0.5:
                    # Only time-stop if we haven't reached breakeven profit
                    logger.info(
                        f"TIME STOP: ticket={ticket} open {meta['bars_open']} bars "
                        f"with only {profit_pts:.2f}pts profit — closing"
                    )
                    self.connector.close_position(ticket)
                    self.untrack(ticket)
                    continue

            # --------------------------------------------------------
            # 3. Breakeven then trail
            # --------------------------------------------------------
            if is_buy:
                if profit_pts >= atr * Config.BREAKEVEN_TRIGGER and sl < entry:
                    new_sl = entry + atr * 0.1
                    if self.connector.modify_position_sl(ticket, new_sl):
                        logger.info(f"Breakeven set: {ticket} SL→{new_sl:.2f}")

                elif profit_pts > atr * Config.BREAKEVEN_TRIGGER:
                    new_sl = current - atr * Config.TRAILING_DISTANCE
                    if new_sl > sl:
                        if self.connector.modify_position_sl(ticket, new_sl):
                            logger.info(f"Trail SL: {ticket}→{new_sl:.2f}")
            else:
                if profit_pts >= atr * Config.BREAKEVEN_TRIGGER and sl > entry:
                    new_sl = entry - atr * 0.1
                    if self.connector.modify_position_sl(ticket, new_sl):
                        logger.info(f"Breakeven set: {ticket} SL→{new_sl:.2f}")

                elif profit_pts > atr * Config.BREAKEVEN_TRIGGER:
                    new_sl = current + atr * Config.TRAILING_DISTANCE
                    if new_sl < sl:
                        if self.connector.modify_position_sl(ticket, new_sl):
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
