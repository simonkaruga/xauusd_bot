"""
Event-Driven Main Loop
========================
Instead of polling every 5 minutes (old approach — signals could be up to
5 minutes stale), this loop watches for new bar closes and fires the
trading cycle within seconds of a candle completing.

Architecture:
  - Tight loop checks last bar timestamp every 10 seconds
  - Fires trading cycle immediately when a new M15 bar closes
  - Separate thread runs the dashboard so it never blocks trading
  - Weekly Sunday auto-retrain of ML classifier
"""

import time
import threading
from datetime import datetime, date
import MetaTrader5 as mt5

from trading_bot import TradingBot
from config import Config
from logger import logger


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _mt5_timeframe(tf_str: str) -> int:
    mapping = {
        'M1': mt5.TIMEFRAME_M1,  'M5': mt5.TIMEFRAME_M5,
        'M15': mt5.TIMEFRAME_M15, 'M30': mt5.TIMEFRAME_M30,
        'H1': mt5.TIMEFRAME_H1,   'H4': mt5.TIMEFRAME_H4,
        'D1': mt5.TIMEFRAME_D1,
    }
    return mapping.get(tf_str, mt5.TIMEFRAME_M15)


def _is_trading_day() -> bool:
    day = datetime.utcnow().weekday()   # Mon=0 … Sun=6
    return day <= 3                     # Mon–Thu


def _is_trading_hour() -> bool:
    hour = datetime.utcnow().hour
    return Config.TRADING_START_HOUR <= hour < Config.TRADING_END_HOUR


def _get_last_bar_time(symbol: str, timeframe: int) -> int | None:
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 1)
    if rates is None or len(rates) == 0:
        return None
    return int(rates[-1]['time'])


def _run_dashboard():
    """Run Flask dashboard in a background thread (non-blocking)."""
    try:
        from dashboard import app
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    except Exception as e:
        logger.warning(f"Dashboard failed to start: {e}")


def _retrain_ml(bot: TradingBot, reason: str) -> bool:
    """Shared retrain logic. Returns True if retrain was attempted."""
    try:
        import pandas as pd
        trades = bot.db.get_trades_since(days=180)
        if len(trades) < 30:
            logger.info(f"ML retrain skipped ({reason}): only {len(trades)} live trades (need 30+)")
            return False

        df = bot.connector.get_bars(Config.SYMBOL, Config.TIMEFRAME, 5000)
        if df is None:
            return False

        df['time'] = pd.to_datetime(df['time'])
        trade_list = []
        for t in trades:
            ts  = pd.Timestamp(t[1])
            idx = df.index[df['time'] <= ts]
            if len(idx):
                profit = t[13] or t[10] or 0
                trade_list.append({'entry_bar': int(idx[-1]), 'profit': profit})

        if len(trade_list) >= 30:
            bot.ml_classifier.train(df, trade_list)
            logger.info(f"ML retrained ({reason}) on {len(trade_list)} live trades")
            return True
        else:
            logger.info(f"ML retrain skipped ({reason}): not enough aligned trades")
            return False
    except Exception as e:
        logger.warning(f"ML retrain error ({reason}): {e}")
        return False


def _maybe_retrain_ml(bot: TradingBot, last_retrain_date: date) -> date:
    """
    Retrain on two triggers:
    1. Every Sunday (scheduled maintenance)
    2. Any time feature drift detection flags needs_retrain=True
    """
    today = date.today()

    # Drift-triggered retrain (urgent — don't wait for Sunday)
    if getattr(bot.ml_classifier, '_drift_warned', False):
        logger.warning("ML retrain triggered by feature drift detection")
        _retrain_ml(bot, reason='drift')
        bot.ml_classifier._drift_warned = False   # reset flag after retrain
        return today

    # Scheduled Sunday retrain
    if today.weekday() == 6 and today != last_retrain_date:
        logger.info("Sunday auto-retrain: retraining ML classifier on live trades")
        _retrain_ml(bot, reason='scheduled-sunday')
        return today

    return last_retrain_date


# -----------------------------------------------------------------------
# Main event loop
# -----------------------------------------------------------------------

def main():
    bot = TradingBot()

    if not bot.start():
        logger.error("Failed to start bot — check MT5 connection")
        return

    # Start dashboard in background thread
    dash_thread = threading.Thread(target=_run_dashboard, daemon=True)
    dash_thread.start()
    logger.info("Dashboard thread started → http://localhost:5000")

    tf = _mt5_timeframe(Config.TIMEFRAME)
    last_bar_time: int | None = None
    last_retrain_date = date.min
    poll_interval = 10  # seconds between bar-time checks

    logger.info(
        f"Event-driven loop started | symbol={Config.SYMBOL} tf={Config.TIMEFRAME} "
        f"| trading hours {Config.TRADING_START_HOUR:02d}:00-{Config.TRADING_END_HOUR:02d}:00 GMT "
        f"| poll every {poll_interval}s"
    )

    try:
        while True:
            # Weekly ML retrain check
            last_retrain_date = _maybe_retrain_ml(bot, last_retrain_date)

            # Only check during active trading session
            if not _is_trading_day() or not _is_trading_hour():
                time.sleep(60)
                continue

            # Check for new bar close
            current_bar_time = _get_last_bar_time(Config.SYMBOL, tf)

            if current_bar_time is None:
                logger.warning("Could not fetch bar time — reconnecting...")
                time.sleep(poll_interval)
                continue

            if last_bar_time is None:
                # First run — record current bar, don't trade yet
                last_bar_time = current_bar_time
                logger.info(f"Baseline bar time set: {datetime.utcfromtimestamp(last_bar_time)}")
                time.sleep(poll_interval)
                continue

            if current_bar_time != last_bar_time:
                # New bar just closed — fire immediately
                elapsed = datetime.utcnow()
                logger.info(
                    f"New {Config.TIMEFRAME} bar closed at "
                    f"{datetime.utcfromtimestamp(current_bar_time)} — running cycle"
                )
                last_bar_time = current_bar_time
                bot.run_once()
                latency_ms = (datetime.utcnow() - elapsed).total_seconds() * 1000
                logger.info(f"Cycle completed in {latency_ms:.0f}ms")

            time.sleep(poll_interval)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received")
        bot.stop()
    except Exception as e:
        logger.error(f"Main loop crashed: {e}", exc_info=True)
        bot.stop()


if __name__ == "__main__":
    main()
