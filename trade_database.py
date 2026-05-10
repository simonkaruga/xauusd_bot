"""
Trade Database
===============
SQLite persistence for all trades and daily stats.
Includes:
  - Session and regime attribution columns
  - close_trade() actually called on exit
  - Performance queries by session, regime, day-of-week
"""

import sqlite3
import os
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TradeDatabase:
    def __init__(self, db_path='logs/trades.db'):
        os.makedirs('logs', exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA cache_size=-8000')
        return conn

    def _init_db(self):
        conn = self._conn()
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT,
                    ticket        INTEGER,
                    symbol        TEXT,
                    type          TEXT,
                    entry_price   REAL,
                    exit_price    REAL,
                    sl            REAL,
                    tp            REAL,
                    volume        REAL,
                    profit        REAL,
                    commission    REAL DEFAULT 0,
                    spread_cost   REAL DEFAULT 0,
                    net_profit    REAL,
                    status        TEXT DEFAULT 'OPEN',
                    session       TEXT,
                    regime        TEXT,
                    confidence    REAL DEFAULT 0,
                    macro_mult    REAL DEFAULT 1.0,
                    ml_score      REAL DEFAULT 0,
                    comment       TEXT,
                    close_time    TEXT
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    date              TEXT UNIQUE,
                    starting_balance  REAL,
                    ending_balance    REAL,
                    total_trades      INTEGER,
                    wins              INTEGER,
                    losses            INTEGER,
                    net_profit        REAL,
                    win_rate          REAL,
                    profit_factor     REAL,
                    max_drawdown_pct  REAL DEFAULT 0,
                    sharpe            REAL DEFAULT 0
                )
            ''')

            # Add missing columns to existing dbs (idempotent)
            self._add_column_if_missing(conn, 'trades', 'session', 'TEXT')
            self._add_column_if_missing(conn, 'trades', 'regime', 'TEXT')
            self._add_column_if_missing(conn, 'trades', 'confidence', 'REAL DEFAULT 0')
            self._add_column_if_missing(conn, 'trades', 'macro_mult', 'REAL DEFAULT 1.0')
            self._add_column_if_missing(conn, 'trades', 'ml_score', 'REAL DEFAULT 0')
            self._add_column_if_missing(conn, 'trades', 'commission', 'REAL DEFAULT 0')
            self._add_column_if_missing(conn, 'trades', 'spread_cost', 'REAL DEFAULT 0')
            self._add_column_if_missing(conn, 'trades', 'net_profit', 'REAL')
            self._add_column_if_missing(conn, 'trades', 'close_time', 'TEXT')
            conn.commit()
        finally:
            conn.close()

    def _add_column_if_missing(self, conn, table, column, col_type):
        # table and col_type are always internal constants, never user input
        try:
            conn.execute('ALTER TABLE {} ADD COLUMN {} {}'.format(table, column, col_type))
        except sqlite3.OperationalError:
            pass  # Column already exists

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def insert_trade(self, ticket, symbol, trade_type, entry, sl, tp, volume,
                     session=None, regime=None, confidence=0.0,
                     macro_mult=1.0, ml_score=0.0, comment=None):
        with self._conn() as conn:
            conn.execute('''
                INSERT INTO trades
                  (timestamp, ticket, symbol, type, entry_price, sl, tp, volume,
                   status, session, regime, confidence, macro_mult, ml_score, comment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?)
            ''', (
                _utcnow().isoformat(), ticket, symbol, trade_type,
                entry, sl, tp, volume,
                session, regime, confidence, macro_mult, ml_score, comment
            ))

    def close_trade(self, ticket, exit_price, profit,
                    commission=0.0, spread_cost=0.0):
        net = profit - commission - spread_cost
        with self._conn() as conn:
            conn.execute('''
                UPDATE trades
                SET exit_price=?, profit=?, commission=?, spread_cost=?,
                    net_profit=?, status='CLOSED', close_time=?
                WHERE ticket=? AND status='OPEN'
            ''', (exit_price, profit, commission, spread_cost, net,
                  _utcnow().isoformat(), ticket))

    def upsert_daily_stats(self, date_str, starting_balance, ending_balance,
                            total_trades, wins, losses, net_profit,
                            max_drawdown_pct=0.0, sharpe=0.0):
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        gross_profit = sum(t[10] for t in self.get_trades_by_date(date_str) if t[10] and t[10] > 0)
        gross_loss = abs(sum(t[10] for t in self.get_trades_by_date(date_str) if t[10] and t[10] < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else 0

        with self._conn() as conn:
            conn.execute('''
                INSERT INTO daily_stats
                  (date, starting_balance, ending_balance, total_trades, wins, losses,
                   net_profit, win_rate, profit_factor, max_drawdown_pct, sharpe)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                  ending_balance=excluded.ending_balance,
                  total_trades=excluded.total_trades,
                  wins=excluded.wins, losses=excluded.losses,
                  net_profit=excluded.net_profit, win_rate=excluded.win_rate,
                  profit_factor=excluded.profit_factor,
                  max_drawdown_pct=excluded.max_drawdown_pct, sharpe=excluded.sharpe
            ''', (date_str, starting_balance, ending_balance, total_trades,
                  wins, losses, net_profit, win_rate, pf, max_drawdown_pct, sharpe))

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get_daily_trades(self, date=None):
        d = date or _utcnow().date().isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM trades WHERE DATE(timestamp)=? AND status='CLOSED'", (d,)
            )
            return cur.fetchall()

    def get_trades_by_date(self, date_str):
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM trades WHERE DATE(timestamp)=? AND status='CLOSED'", (date_str,)
            )
            return cur.fetchall()

    def get_all_trades(self, limit=200):
        with self._conn() as conn:
            cur = conn.execute(
                'SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?', (limit,)
            )
            return cur.fetchall()

    def get_open_trades(self):
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM trades WHERE status='OPEN'")
            return cur.fetchall()

    def get_trades_since(self, days=30):
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM trades WHERE status='CLOSED' "
                "AND timestamp >= datetime('now', ?)",
                (f'-{days} days',)
            )
            return cur.fetchall()

    def get_performance_by_session(self):
        with self._conn() as conn:
            cur = conn.execute('''
                SELECT session,
                       COUNT(*) as trades,
                       SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) as wins,
                       ROUND(SUM(net_profit), 2) as total_pnl
                FROM trades WHERE status='CLOSED' AND session IS NOT NULL
                GROUP BY session
            ''')
            return cur.fetchall()

    def get_performance_by_regime(self):
        with self._conn() as conn:
            cur = conn.execute('''
                SELECT regime,
                       COUNT(*) as trades,
                       SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) as wins,
                       ROUND(SUM(net_profit), 2) as total_pnl
                FROM trades WHERE status='CLOSED' AND regime IS NOT NULL
                GROUP BY regime
            ''')
            return cur.fetchall()

    def get_equity_curve(self, limit=500):
        with self._conn() as conn:
            cur = conn.execute('''
                SELECT timestamp, net_profit
                FROM trades WHERE status='CLOSED'
                ORDER BY timestamp ASC LIMIT ?
            ''', (limit,))
            rows = cur.fetchall()
        if not rows:
            return []
        cumulative = 0.0
        curve = []
        for ts, pnl in rows:
            cumulative += (pnl or 0)
            curve.append({'time': ts, 'cumulative_pnl': round(cumulative, 2)})
        return curve

    def get_stats_summary(self):
        with self._conn() as conn:
            cur = conn.execute('''
                SELECT
                  COUNT(*) as total,
                  SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) as wins,
                  ROUND(SUM(net_profit), 2) as net_pnl,
                  ROUND(AVG(CASE WHEN net_profit > 0 THEN net_profit END), 2) as avg_win,
                  ROUND(AVG(CASE WHEN net_profit < 0 THEN net_profit END), 2) as avg_loss
                FROM trades WHERE status='CLOSED'
            ''')
            row = cur.fetchone()
        if not row or not row[0]:
            return {}
        total, wins, net_pnl, avg_win, avg_loss = row
        win_rate = (wins / total * 100) if total else 0
        avg_loss_abs = abs(avg_loss) if avg_loss else 0
        pf = ((wins * (avg_win or 0)) / ((total - wins) * avg_loss_abs)) \
            if (total - wins) > 0 and avg_loss_abs > 0 else 0
        return {
            'total_trades': total,
            'wins': wins,
            'win_rate': round(win_rate, 1),
            'net_pnl': net_pnl,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': round(pf, 2),
        }
