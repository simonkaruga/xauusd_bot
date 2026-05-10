"""
ML Bootstrap Trainer
=====================
Run this ONCE before starting the live bot to pre-train the ML signal
classifier on historical backtest data.

Usage:
    python train_from_backtest.py

The trained model is saved to logs/signal_classifier.pkl and will be
loaded automatically by the live bot on startup.
"""

import sys
import pandas as pd
from backtester import Backtester
from ml_signal_classifier import MLSignalClassifier
from mt5_connector import MT5Connector
from config import Config
from logger import logger


def main():
    connector = MT5Connector()
    if not connector.connect():
        logger.error("Failed to connect to MT5 — is the terminal running?")
        sys.exit(1)

    logger.info("Fetching historical data for ML training...")
    df = connector.get_bars(Config.SYMBOL, Config.TIMEFRAME, 10_000)
    connector.disconnect()

    if df is None or len(df) < 200:
        logger.error("Insufficient historical data")
        sys.exit(1)

    logger.info(f"Running backtest on {len(df)} bars to generate training labels...")
    bt = Backtester()
    # Run on pre-fetched data (no second MT5 connection needed)
    result = bt.run_on_df(df, initial_balance=Config.SIMULATED_BALANCE)

    if not result or 'error' in result:
        logger.error(f"Backtest failed: {result}")
        sys.exit(1)

    trades = bt.trades
    logger.info(f"Backtest complete: {len(trades)} trades | "
                f"WR={result['win_rate']}% | PF={result['profit_factor']}")

    if len(trades) < 30:
        logger.warning(
            f"Only {len(trades)} trades generated — need 30+ for reliable ML training. "
            "Try increasing the data window or relaxing strategy filters."
        )
        sys.exit(1)

    # Convert backtest trades to training format
    trade_list = [
        {'entry_bar': t['entry_bar'], 'profit': t['profit']}
        for t in trades
        if 'entry_bar' in t
    ]

    logger.info(f"Training ML classifier on {len(trade_list)} trades...")
    clf = MLSignalClassifier()
    df['time'] = pd.to_datetime(df['time'])
    success = clf.train(df, trade_list)

    if success:
        logger.info("ML model trained and saved to logs/signal_classifier.pkl")
        logger.info("You can now start the live bot with: python main.py")
    else:
        logger.error("ML training failed — check logs for details")
        sys.exit(1)


if __name__ == "__main__":
    main()
