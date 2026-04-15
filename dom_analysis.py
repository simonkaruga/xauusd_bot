"""
DOM (Depth of Market) Analysis
================================
Uses MT5's market_book_get() to read Level 2 order book data.

What it detects:
  - Book imbalance: when bid depth >> ask depth, large buyers are absorbing
    supply — confirms a BUY signal. Vice versa for SELL.
  - Absorption: a large bid/ask level that hasn't moved despite price hitting it
    indicates institutional limit orders defending a level.
  - Thin book: low total depth = high volatility risk — reduce position size.

Score: -1.0 (heavy sell pressure) to +1.0 (heavy buy pressure)
Confidence multiplier: 0.5 (thin book) to 1.2 (strong absorption confirmation)

MT5 requirement: broker must provide Level 2 data (most ECN brokers do).
If DOM not available, falls back to neutral (1.0 multiplier, no block).
"""

import MetaTrader5 as mt5
from logger import logger


class DOMAnalysis:
    def __init__(self):
        self.imbalance_threshold = 0.65   # 65% one side = significant imbalance
        self.thin_book_threshold = 10     # fewer than 10 levels total = thin

    # ------------------------------------------------------------------
    # Subscribe / unsubscribe
    # ------------------------------------------------------------------
    def enable(self, symbol: str) -> bool:
        ok = mt5.market_book_add(symbol)
        if ok:
            logger.info(f"DOM: subscribed to {symbol}")
        else:
            logger.warning(f"DOM: subscription failed for {symbol} — broker may not support L2")
        return ok

    def disable(self, symbol: str):
        mt5.market_book_release(symbol)

    # ------------------------------------------------------------------
    # Main analysis
    # ------------------------------------------------------------------
    def analyse(self, symbol: str) -> dict:
        """
        Returns:
          imbalance_score  : -1.0 to +1.0
          confirms_buy     : True if book strongly favours buyers
          confirms_sell    : True if book strongly favours sellers
          size_multiplier  : 0.5 (thin) to 1.2 (deep, confirmed)
          available        : False if DOM not supported by broker
        """
        book = mt5.market_book_get(symbol)

        if not book:
            return self._neutral()

        bids = [b for b in book if b.type == mt5.BOOK_TYPE_BUY]
        asks = [b for b in book if b.type == mt5.BOOK_TYPE_SELL]

        if not bids and not asks:
            return self._neutral()

        total_bid_vol = sum(b.volume for b in bids)
        total_ask_vol = sum(b.volume for b in asks)
        total_vol = total_bid_vol + total_ask_vol

        if total_vol == 0:
            return self._neutral()

        bid_ratio = total_bid_vol / total_vol   # 0→1, >0.5 = more bids

        # Imbalance score: +1 = all bids, -1 = all asks
        imbalance = bid_ratio * 2 - 1

        # Depth quality
        n_levels = len(bids) + len(asks)
        if n_levels < self.thin_book_threshold:
            size_mult = 0.6   # thin book — reduce size (higher slippage risk)
        elif abs(imbalance) > self.imbalance_threshold - 0.1:
            size_mult = 1.15  # strong imbalance confirms direction — slight size boost
        else:
            size_mult = 1.0

        confirms_buy = imbalance > self.imbalance_threshold
        confirms_sell = imbalance < -self.imbalance_threshold

        result = {
            'available': True,
            'imbalance_score': round(imbalance, 3),
            'bid_volume': round(total_bid_vol, 2),
            'ask_volume': round(total_ask_vol, 2),
            'n_levels': n_levels,
            'confirms_buy': confirms_buy,
            'confirms_sell': confirms_sell,
            'size_multiplier': size_mult,
        }

        logger.info(
            f"DOM {symbol}: imbalance={imbalance:+.3f} "
            f"bids={total_bid_vol:.0f} asks={total_ask_vol:.0f} "
            f"levels={n_levels} mult={size_mult:.2f}"
        )
        return result

    def check_signal(self, signal_type: str, symbol: str) -> tuple:
        """
        Returns (allowed: bool, size_multiplier: float).
        Blocks signal if DOM strongly contradicts direction.
        """
        dom = self.analyse(symbol)

        if not dom['available']:
            return True, 1.0   # No DOM data — pass through

        if signal_type == 'BUY':
            if dom['confirms_sell']:
                logger.info(f"DOM blocked BUY: heavy ask-side pressure (score={dom['imbalance_score']:+.3f})")
                return False, 0.5
            mult = dom['size_multiplier'] if dom['confirms_buy'] else 1.0
            return True, mult

        elif signal_type == 'SELL':
            if dom['confirms_buy']:
                logger.info(f"DOM blocked SELL: heavy bid-side pressure (score={dom['imbalance_score']:+.3f})")
                return False, 0.5
            mult = dom['size_multiplier'] if dom['confirms_sell'] else 1.0
            return True, mult

        return True, 1.0

    def _neutral(self) -> dict:
        return {
            'available': False,
            'imbalance_score': 0.0,
            'bid_volume': 0.0,
            'ask_volume': 0.0,
            'n_levels': 0,
            'confirms_buy': False,
            'confirms_sell': False,
            'size_multiplier': 1.0,
        }
