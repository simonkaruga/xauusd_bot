import MetaTrader5 as mt5
from config import Config
from logger import logger

class PositionMonitor:
    def __init__(self, connector):
        self.connector = connector
        self.tracked = {}  # ticket -> atr

    def track(self, ticket, atr):
        self.tracked[ticket] = atr

    def untrack(self, ticket):
        self.tracked.pop(ticket, None)

    def update_trailing_stops(self):
        if not Config.ENABLE_TRAILING_STOP:
            return

        positions = self.connector.get_positions(Config.SYMBOL)

        for pos in positions:
            ticket = pos.ticket
            atr = self.tracked.get(ticket)
            if not atr:
                continue

            entry = pos.price_open
            current = pos.price_current
            sl = pos.sl

            if pos.type == mt5.ORDER_TYPE_BUY:
                profit_pts = current - entry

                # Move to breakeven
                if profit_pts >= atr * Config.BREAKEVEN_TRIGGER and sl < entry:
                    new_sl = entry + (atr * 0.1)
                    if self.connector.modify_position_sl(ticket, new_sl):
                        logger.info(f"Breakeven set: {ticket} SL -> {new_sl:.2f}")

                # Trail stop
                elif profit_pts > atr * Config.BREAKEVEN_TRIGGER:
                    new_sl = current - (atr * Config.TRAILING_DISTANCE)
                    if new_sl > sl:
                        if self.connector.modify_position_sl(ticket, new_sl):
                            logger.info(f"Trailing SL: {ticket} -> {new_sl:.2f}")

            elif pos.type == mt5.ORDER_TYPE_SELL:
                profit_pts = entry - current

                # Move to breakeven
                if profit_pts >= atr * Config.BREAKEVEN_TRIGGER and sl > entry:
                    new_sl = entry - (atr * 0.1)
                    if self.connector.modify_position_sl(ticket, new_sl):
                        logger.info(f"Breakeven set: {ticket} SL -> {new_sl:.2f}")

                # Trail stop
                elif profit_pts > atr * Config.BREAKEVEN_TRIGGER:
                    new_sl = current + (atr * Config.TRAILING_DISTANCE)
                    if new_sl < sl:
                        if self.connector.modify_position_sl(ticket, new_sl):
                            logger.info(f"Trailing SL: {ticket} -> {new_sl:.2f}")
