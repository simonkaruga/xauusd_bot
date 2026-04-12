import sqlite3
import os
from datetime import datetime

class TradeDatabase:
    def __init__(self, db_path='logs/trades.db'):
        os.makedirs('logs', exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                ticket INTEGER,
                symbol TEXT,
                type TEXT,
                entry_price REAL,
                exit_price REAL,
                sl REAL,
                tp REAL,
                volume REAL,
                profit REAL,
                status TEXT,
                comment TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE,
                starting_balance REAL,
                ending_balance REAL,
                total_trades INTEGER,
                wins INTEGER,
                losses INTEGER,
                net_profit REAL
            )
        ''')

        conn.commit()
        conn.close()

    def insert_trade(self, ticket, symbol, trade_type, entry, sl, tp, volume):
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            INSERT INTO trades (timestamp, ticket, symbol, type, entry_price, sl, tp, volume, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        ''', (datetime.now().isoformat(), ticket, symbol, trade_type, entry, sl, tp, volume))
        conn.commit()
        conn.close()

    def close_trade(self, ticket, exit_price, profit):
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            UPDATE trades SET exit_price=?, profit=?, status='CLOSED'
            WHERE ticket=? AND status='OPEN'
        ''', (exit_price, profit, ticket))
        conn.commit()
        conn.close()

    def get_daily_trades(self, date=None):
        if date is None:
            date = datetime.now().date().isoformat()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM trades WHERE DATE(timestamp)=? AND status='CLOSED'
        ''', (date,))
        trades = cursor.fetchall()
        conn.close()
        return trades

    def get_all_trades(self, limit=100):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?', (limit,))
        trades = cursor.fetchall()
        conn.close()
        return trades
