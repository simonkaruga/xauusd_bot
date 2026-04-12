import pandas as pd
from datetime import datetime
from mt5_connector import MT5Connector
from strategy import TrendFollowingStrategy
from config import Config
from logger import logger

class Backtester:
    def __init__(self):
        self.connector = MT5Connector()
        self.strategy = TrendFollowingStrategy()
        self.trades = []

    def run(self, symbol='XAUUSD', days=30, initial_balance=100):
        if not self.connector.connect():
            logger.error("Failed to connect for backtesting")
            return None

        logger.info(f"Starting backtest: {symbol} | {days} days | ${initial_balance}")

        bars_needed = days * 24 * 4  # 15-min bars
        df = self.connector.get_bars(symbol, Config.TIMEFRAME, bars_needed)

        if df is None or len(df) < 100:
            logger.error("Insufficient data for backtesting")
            self.connector.disconnect()
            return None

        self.trades = []
        balance = initial_balance
        position = None

        for i in range(100, len(df)):
            window = df.iloc[:i+1].copy()
            current_price = window.iloc[-1]['close']

            # Check SL/TP on open position
            if position:
                if position['type'] == 'BUY':
                    if current_price <= position['sl']:
                        balance += self._close(position, position['sl'])
                        position = None
                    elif current_price >= position['tp']:
                        balance += self._close(position, position['tp'])
                        position = None
                else:
                    if current_price >= position['sl']:
                        balance += self._close(position, position['sl'])
                        position = None
                    elif current_price <= position['tp']:
                        balance += self._close(position, position['tp'])
                        position = None

            # Generate signal
            if not position:
                signal = self.strategy.generate_signal(window)
                if signal:
                    risk_amount = balance * Config.RISK_PER_TRADE
                    price_diff = abs(signal['price'] - signal['sl'])
                    size = max(0.01, round(risk_amount / (price_diff * 100), 2))

                    position = {
                        'type': signal['type'],
                        'entry': signal['price'],
                        'sl': signal['sl'],
                        'tp': signal['tp'],
                        'size': size,
                        'time': window.iloc[-1]['time']
                    }

        # Close remaining position
        if position:
            balance += self._close(position, df.iloc[-1]['close'])

        self.connector.disconnect()
        return self._report(initial_balance, balance)

    def _close(self, position, exit_price):
        multiplier = 1 if position['type'] == 'BUY' else -1
        profit = (exit_price - position['entry']) * multiplier * position['size'] * 100
        self.trades.append({**position, 'exit': exit_price, 'profit': profit})
        return profit

    def _report(self, initial_balance, final_balance):
        if not self.trades:
            return {'error': 'No trades executed'}

        wins = [t for t in self.trades if t['profit'] > 0]
        losses = [t for t in self.trades if t['profit'] <= 0]
        total = len(self.trades)
        gross_profit = sum(t['profit'] for t in wins)
        gross_loss = abs(sum(t['profit'] for t in losses))
        net = final_balance - initial_balance

        report = {
            'initial_balance': initial_balance,
            'final_balance': round(final_balance, 2),
            'net_profit': round(net, 2),
            'return_pct': round(net / initial_balance * 100, 2),
            'total_trades': total,
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': round(len(wins) / total * 100, 2) if total > 0 else 0,
            'profit_factor': round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
            'gross_profit': round(gross_profit, 2),
            'gross_loss': round(gross_loss, 2),
            'avg_win': round(gross_profit / len(wins), 2) if wins else 0,
            'avg_loss': round(gross_loss / len(losses), 2) if losses else 0,
        }

        logger.info("=== BACKTEST RESULTS ===")
        logger.info(f"Return: ${report['net_profit']} ({report['return_pct']}%)")
        logger.info(f"Win Rate: {report['win_rate']}% ({report['wins']}W / {report['losses']}L)")
        logger.info(f"Profit Factor: {report['profit_factor']}")

        return report

if __name__ == "__main__":
    bt = Backtester()
    results = bt.run(Config.SYMBOL, days=30, initial_balance=100)
    if results and 'error' not in results:
        print(f"\nReturn: {results['return_pct']}%")
        print(f"Win Rate: {results['win_rate']}%")
        print(f"Profit Factor: {results['profit_factor']}")
