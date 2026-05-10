import requests
from config import Config
from logger import logger

class TelegramNotifier:
    def __init__(self):
        self.enabled = Config.TELEGRAM_ENABLED
        self.bot_token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def send(self, message):
        if not self.enabled or not self.bot_token or not self.chat_id:
            return False
        try:
            response = requests.post(self.base_url, data={
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram failed: {e}")
            return False

    def notify_trade(self, trade_type, symbol, price, sl, tp, volume):
        emoji = '🟢' if trade_type == 'BUY' else '🔴'
        rr = abs(tp - price) / abs(sl - price) if abs(sl - price) > 0 else 0
        self.send(f"""
{emoji} <b>{trade_type} {symbol}</b>
💰 Entry: {price:.2f}
🛑 SL: {sl:.2f}
🎯 TP: {tp:.2f}
📊 Lots: {volume} | R:R 1:{rr:.1f}
        """.strip())

    def notify_close(self, symbol, profit, reason=""):
        emoji = '✅' if profit > 0 else '❌'
        self.send(f"{emoji} <b>Closed {symbol}</b>\n💵 P&L: ${profit:.2f}\n{reason}".strip())

    def notify_circuit_breaker(self, loss_pct):
        self.send(f"🚨 <b>CIRCUIT BREAKER</b>\nDaily loss: {loss_pct:.1f}%\nTrading stopped for today.")

    def notify_daily_summary(self, trades, profit, win_rate, balance):
        self.send(f"""
📊 <b>Daily Summary</b>
💰 Balance: ${balance:.2f}
📈 P&L: ${profit:.2f}
🎯 Trades: {trades} | Win Rate: {win_rate:.1f}%
        """.strip())

    def notify_error(self, error_msg):
        self.send(f"⚠️ <b>Bot Error</b>\n{error_msg}")

    def send_message(self, message: str):
        """Alias for send() — used by trading_bot for TP/SL hit alerts."""
        self.send(message)
