"""
Dual Logger
============
- Plain text → logs/trading_bot.log  (human readable)
- Structured JSON → logs/trading_bot.jsonl  (machine queryable)

JSON format per line:
  {"ts": "2024-01-15T12:03:45", "level": "INFO", "msg": "...", "extra": {...}}

Query examples (bash):
  grep '"level":"ERROR"' logs/trading_bot.jsonl | jq .
  cat logs/trading_bot.jsonl | jq 'select(.msg | contains("TRADE"))'
"""

import logging
import json
import os
from datetime import datetime, timezone
from config import Config

os.makedirs('logs', exist_ok=True)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            'ts': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
        }
        if record.exc_info:
            log_entry['exc'] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logger() -> logging.Logger:
    logger = logging.getLogger('TradingBot')
    logger.setLevel(getattr(logging, Config.LOG_LEVEL, logging.INFO))

    if logger.handlers:
        return logger  # Already configured (prevent duplicate handlers on re-import)

    plain_fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # 1. Plain text file
    fh = logging.FileHandler(Config.LOG_FILE)
    fh.setFormatter(plain_fmt)
    logger.addHandler(fh)

    # 2. JSON file (newline-delimited, easy to grep/jq)
    json_path = Config.LOG_FILE.replace('.log', '.jsonl')
    jh = logging.FileHandler(json_path)
    jh.setFormatter(_JsonFormatter())
    logger.addHandler(jh)

    # 3. Console
    ch = logging.StreamHandler()
    ch.setFormatter(plain_fmt)
    logger.addHandler(ch)

    return logger


logger = setup_logger()
