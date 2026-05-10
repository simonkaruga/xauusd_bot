"""
Signal Broadcasting System
============================
Broadcasts trade signals to a public Telegram channel and/or a
copy-trading webhook. Configure via .env:

  SIGNAL_CHANNEL_ID=@YourChannel  (or a numeric chat ID)
  SIGNAL_WEBHOOK_URL=https://...  (optional)
"""

import requests
from datetime import datetime, timezone
from config import Config
from logger import logger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SignalBroadcaster:
    def __init__(self):
        self.channel_id = getattr(Config, 'SIGNAL_CHANNEL_ID', '')
        self.webhook_url = getattr(Config, 'SIGNAL_WEBHOOK_URL', '')
        self.bot_token = Config.TELEGRAM_BOT_TOKEN
        self.signal_history = []

    def broadcast_signal(self, signal: dict, symbol: str):
        message = self._format_signal(signal, symbol)
        self._send_to_telegram_channel(message)
        self._send_to_webhook(signal, symbol)
        self.signal_history.append({
            'time': _utcnow().isoformat(),
            'type': signal['type'],
            'symbol': symbol,
            'entry': signal['price'],
            'sl': signal['sl'],
            'tp': signal['tp'],
        })
        logger.info(f"Signal broadcasted: {signal['type']} {symbol} @ {signal['price']:.2f}")

    def _format_signal(self, signal: dict, symbol: str) -> str:
        emoji = '🟢' if signal['type'] == 'BUY' else '🔴'
        sl_dist = abs(signal['price'] - signal['sl'])
        rr = abs(signal['tp'] - signal['price']) / sl_dist if sl_dist > 0 else 0
        return (
            f"{emoji} <b>{signal['type']} {symbol}</b>\n"
            f"Entry: <b>{signal['price']:.2f}</b>\n"
            f"SL: {signal['sl']:.2f}  |  TP: {signal['tp']:.2f}\n"
            f"R:R 1:{rr:.1f}  |  Conf: {signal.get('confidence', 0) * 100:.0f}%\n"
            f"Regime: {signal.get('regime', '—')}\n"
            f"<i>{_utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>"
        )

    def _send_to_telegram_channel(self, message: str):
        """Post to a public/private Telegram channel using the bot token from config."""
        if not self.channel_id or not self.bot_token:
            return
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            resp = requests.post(url, data={
                'chat_id': self.channel_id,
                'text': message,
                'parse_mode': 'HTML',
            }, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"Signal channel post failed: {resp.status_code} {resp.text[:100]}")
        except Exception as e:
            logger.error(f"Signal channel error: {e}")

    def _send_to_webhook(self, signal: dict, symbol: str):
        if not self.webhook_url:
            return
        try:
            requests.post(self.webhook_url, json={
                'symbol': symbol,
                'action': signal['type'],
                'entry': signal['price'],
                'sl': signal['sl'],
                'tp': signal['tp'],
                'timestamp': _utcnow().isoformat(),
            }, timeout=5)
        except Exception as e:
            logger.error(f"Webhook failed: {e}")

    def get_performance_summary(self) -> dict:
        if not self.signal_history:
            return {}
        total = len(self.signal_history)
        buys = sum(1 for s in self.signal_history if s['type'] == 'BUY')
        return {'total_signals': total, 'buys': buys, 'sells': total - buys}
