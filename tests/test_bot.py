"""
Test Suite for XAU/USD Trading Bot
=====================================
Run: pytest tests/ -v

Covers:
  - RiskManager: position sizing, Kelly, circuit breaker, streak scaling, state persistence
  - TradeDatabase: insert, close, stats, equity curve, attribution queries
  - CorrelationFilter: check() always returns 3 values, macro logic
  - AdvancedStrategy: regime detection, signal structure
  - Config: validate_config() raises on bad input
"""

import os
import sys
import json
import tempfile
import pytest
import pandas as pd
import numpy as np

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """TradeDatabase backed by a temp file — isolated per test."""
    from trade_database import TradeDatabase
    return TradeDatabase(db_path=str(tmp_path / 'test_trades.db'))


@pytest.fixture
def risk_mgr(tmp_path, monkeypatch):
    """RiskManager with state file in temp dir."""
    monkeypatch.setattr('risk_manager._STATE_PATH', str(tmp_path / 'risk_state.json'))
    from risk_manager import RiskManager
    return RiskManager()


@pytest.fixture
def ohlcv_df():
    """200-bar synthetic M15 OHLCV dataframe."""
    rng = np.random.default_rng(42)
    n = 200
    close = 2000 + np.cumsum(rng.normal(0, 2, n))
    close = np.abs(close) + 1800
    high = close + rng.uniform(0.5, 3, n)
    low = close - rng.uniform(0.5, 3, n)
    return pd.DataFrame({
        'time': pd.date_range('2024-01-01 12:00', periods=n, freq='15min'),
        'open': close - rng.normal(0, 1, n),
        'high': high,
        'low': low,
        'close': close,
        'tick_volume': rng.integers(100, 1000, n),
    })


# ---------------------------------------------------------------------------
# RiskManager tests
# ---------------------------------------------------------------------------

class TestRiskManager:

    def test_initial_state(self, risk_mgr):
        assert risk_mgr.winning_streak == 0
        assert risk_mgr.losing_streak == 0
        assert risk_mgr._trade_profits == []
        assert risk_mgr._open_risk == {}

    def test_position_size_basic(self, risk_mgr):
        risk_mgr.reset_daily_tracking(10000)
        size = risk_mgr.calculate_position_size(
            account_balance=10000,
            entry_price=2000.0,
            sl_price=1998.0,  # $2 SL
        )
        assert size >= 0.01
        assert size <= 10.0  # sanity upper bound

    def test_position_size_respects_hard_cap(self, risk_mgr):
        from config import Config
        risk_mgr.reset_daily_tracking(100000)
        size = risk_mgr.calculate_position_size(
            account_balance=100000,
            entry_price=2000.0,
            sl_price=1999.9,  # tiny SL — would produce huge size without cap
        )
        # Hard cap: MAX_RISK_PER_TRADE=2% of SIMULATED_BALANCE=$1000 → $20 max risk
        max_risk_dollars = Config.SIMULATED_BALANCE * Config.MAX_RISK_PER_TRADE
        price_diff = abs(2000.0 - 1999.9)
        max_size = max_risk_dollars / (price_diff * 100)
        assert size <= max_size * 1.05  # allow 5% float tolerance

    def test_circuit_breaker_triggers(self, risk_mgr):
        risk_mgr.reset_daily_tracking(1000)
        risk_mgr.update_daily_loss(960)  # 4% loss > 3% MAX_DAILY_LOSS
        assert risk_mgr.circuit_breaker_active is True
        assert risk_mgr.can_trade(0, 960) is False

    def test_circuit_breaker_not_triggered_small_loss(self, risk_mgr):
        risk_mgr.reset_daily_tracking(1000)
        risk_mgr.update_daily_loss(995)  # 0.5% loss
        assert risk_mgr.circuit_breaker_active is False

    def test_streak_scaling_reduces_on_losses(self, risk_mgr):
        risk_mgr.reset_daily_tracking(1000)
        # Simulate 4 consecutive losses
        for _ in range(4):
            risk_mgr.deregister_trade(999, profit=-10.0)
        assert risk_mgr.losing_streak == 4
        size_after_losses = risk_mgr.calculate_position_size(1000, 2000, 1998)

        # Reset and get baseline size
        risk_mgr2 = risk_mgr.__class__.__new__(risk_mgr.__class__)
        risk_mgr2.__init__()
        risk_mgr2.reset_daily_tracking(1000)
        baseline_size = risk_mgr2.calculate_position_size(1000, 2000, 1998)

        assert size_after_losses <= baseline_size

    def test_deregister_updates_streaks(self, risk_mgr):
        risk_mgr.deregister_trade(1, profit=50.0)
        assert risk_mgr.winning_streak == 1
        assert risk_mgr.losing_streak == 0
        risk_mgr.deregister_trade(2, profit=-20.0)
        assert risk_mgr.winning_streak == 0
        assert risk_mgr.losing_streak == 1

    def test_portfolio_heat_blocks_new_trade(self, risk_mgr):
        risk_mgr.reset_daily_tracking(1000)
        # Register a trade that uses up all portfolio heat (3% of $1000 = $30)
        risk_mgr.register_open_trade(ticket=1, entry_price=2000, sl_price=1997, position_size=0.10)
        # heat = 3 * 0.10 * 100 = $30 = 3% of $1000 → at limit
        assert risk_mgr.can_trade(0, 1000) is False

    def test_state_persists_and_reloads(self, tmp_path, monkeypatch):
        state_path = str(tmp_path / 'risk_state.json')
        monkeypatch.setattr('risk_manager._STATE_PATH', state_path)
        from risk_manager import RiskManager

        rm1 = RiskManager()
        rm1.reset_daily_tracking(1000)
        rm1.deregister_trade(1, profit=100.0)
        rm1.deregister_trade(2, profit=50.0)
        rm1.deregister_trade(3, profit=-20.0)
        # State should be saved

        rm2 = RiskManager()  # fresh instance — loads from file
        assert rm2.winning_streak == rm1.winning_streak
        assert rm2.losing_streak == rm1.losing_streak
        assert len(rm2._trade_profits) == len(rm1._trade_profits)

    def test_validate_rr(self, risk_mgr):
        # MIN_RISK_REWARD = 2.5; risk=2pts, reward must be >= 5pts
        assert risk_mgr.validate_risk_reward(2000, 1998, 2005) is True   # 2.5R ✓
        assert risk_mgr.validate_risk_reward(2000, 1998, 2003) is False  # 1.5R < 2.5 min


# ---------------------------------------------------------------------------
# TradeDatabase tests
# ---------------------------------------------------------------------------

class TestTradeDatabase:

    def test_insert_and_retrieve(self, tmp_db):
        tmp_db.insert_trade(
            ticket=1001, symbol='XAUUSD', trade_type='BUY',
            entry=2000.0, sl=1998.0, tp=2004.0, volume=0.01,
            session='London_NY', regime='trending',
        )
        open_trades = tmp_db.get_open_trades()
        assert len(open_trades) == 1
        assert open_trades[0][2] == 1001  # ticket
        assert open_trades[0][3] == 'XAUUSD'

    def test_close_trade(self, tmp_db):
        tmp_db.insert_trade(1002, 'XAUUSD', 'BUY', 2000, 1998, 2004, 0.01)
        tmp_db.close_trade(1002, exit_price=2004.0, profit=4.0)
        open_trades = tmp_db.get_open_trades()
        assert len(open_trades) == 0
        all_trades = tmp_db.get_all_trades()
        closed = [t for t in all_trades if t[14] == 'CLOSED']
        assert len(closed) == 1
        assert closed[0][6] == 2004.0  # exit_price

    def test_stats_summary_empty(self, tmp_db):
        stats = tmp_db.get_stats_summary()
        assert stats == {}

    def test_stats_summary_with_trades(self, tmp_db):
        for i, (profit, exit_p) in enumerate([(10, 2004), (-5, 1998), (8, 2003)]):
            tmp_db.insert_trade(2000 + i, 'XAUUSD', 'BUY', 2000, 1998, 2004, 0.01)
            tmp_db.close_trade(2000 + i, exit_price=exit_p, profit=profit)
        stats = tmp_db.get_stats_summary()
        assert stats['total_trades'] == 3
        assert stats['wins'] == 2
        assert abs(stats['win_rate'] - 66.7) < 0.1
        assert stats['net_pnl'] == pytest.approx(13.0, abs=0.01)

    def test_equity_curve(self, tmp_db):
        for i, profit in enumerate([10, -5, 8]):
            tmp_db.insert_trade(3000 + i, 'XAUUSD', 'BUY', 2000, 1998, 2004, 0.01)
            tmp_db.close_trade(3000 + i, exit_price=2002, profit=profit)
        curve = tmp_db.get_equity_curve()
        assert len(curve) == 3
        assert curve[-1]['cumulative_pnl'] == pytest.approx(13.0, abs=0.01)

    def test_performance_by_session(self, tmp_db):
        for i, session in enumerate(['London_NY', 'London_NY', 'Asia']):
            tmp_db.insert_trade(4000 + i, 'XAUUSD', 'BUY', 2000, 1998, 2004, 0.01,
                                session=session)
            tmp_db.close_trade(4000 + i, exit_price=2003, profit=5.0)
        rows = tmp_db.get_performance_by_session()
        sessions = {r[0]: r for r in rows}
        assert 'London_NY' in sessions
        assert sessions['London_NY'][1] == 2  # 2 trades

    def test_no_duplicate_close(self, tmp_db):
        tmp_db.insert_trade(5000, 'XAUUSD', 'BUY', 2000, 1998, 2004, 0.01)
        tmp_db.close_trade(5000, exit_price=2004, profit=4.0)
        tmp_db.close_trade(5000, exit_price=2004, profit=4.0)  # second call — no-op
        all_trades = tmp_db.get_all_trades()
        closed = [t for t in all_trades if t[14] == 'CLOSED']
        assert len(closed) == 1


# ---------------------------------------------------------------------------
# CorrelationFilter tests
# ---------------------------------------------------------------------------

class TestCorrelationFilter:

    def _make_df(self, n=50, trend='flat'):
        rng = np.random.default_rng(0)
        if trend == 'up':
            close = np.linspace(1.08, 1.10, n) + rng.normal(0, 0.0005, n)
        elif trend == 'down':
            close = np.linspace(1.10, 1.08, n) + rng.normal(0, 0.0005, n)
        else:
            close = np.full(n, 1.09) + rng.normal(0, 0.0005, n)
        return pd.DataFrame({
            'time': pd.date_range('2024-01-01', periods=n, freq='15min'),
            'open': close, 'high': close + 0.001,
            'low': close - 0.001, 'close': close,
            'tick_volume': rng.integers(100, 500, n),
        })

    def test_check_returns_three_values(self):
        """Critical: trading_bot unpacks 3 values from check()."""
        from correlation_filter import CorrelationFilter
        cf = CorrelationFilter(connector=None)
        gold_df = self._make_df(50)
        result = cf.check('BUY', gold_df)
        assert len(result) == 3, "check() must return (allowed, multiplier, reason)"

    def test_check_returns_correct_types(self):
        from correlation_filter import CorrelationFilter
        cf = CorrelationFilter(connector=None)
        gold_df = self._make_df(50)
        allowed, multiplier, reason = cf.check('BUY', gold_df)
        assert isinstance(allowed, bool)
        assert isinstance(multiplier, float)
        assert isinstance(reason, str)

    def test_no_usd_data_passthrough(self):
        from correlation_filter import CorrelationFilter
        cf = CorrelationFilter(connector=None)
        gold_df = self._make_df(50)
        allowed, mult, reason = cf.is_macro_aligned('BUY', gold_df, None)
        assert allowed is True
        assert mult == 1.0

    def test_usd_rising_blocks_buy(self):
        from correlation_filter import CorrelationFilter
        cf = CorrelationFilter(connector=None)
        gold_df = self._make_df(50)
        # EURUSD falling = USD rising = headwind for gold BUY
        usd_df = self._make_df(50, trend='down')
        allowed, mult, reason = cf.is_macro_aligned('BUY', gold_df, usd_df)
        # Strong USD rising should block or reduce BUY
        assert mult <= 1.0

    def test_usd_falling_boosts_buy(self):
        from correlation_filter import CorrelationFilter
        cf = CorrelationFilter(connector=None)
        gold_df = self._make_df(50)
        usd_df = self._make_df(50, trend='up')  # EURUSD rising = USD falling = tailwind
        allowed, mult, reason = cf.is_macro_aligned('BUY', gold_df, usd_df)
        assert allowed is True
        assert mult >= 1.0


# ---------------------------------------------------------------------------
# AdvancedStrategy tests
# ---------------------------------------------------------------------------

class TestAdvancedStrategy:

    def _unlock_bullish_macro(self, strat):
        """Inject two rising COT readings so all macro gates open for BUY."""
        strat.set_macro_context(macro_score=0.50, macro_direction='bullish',
                                cot_score=0.30, sentiment_score=0.1)
        strat.set_macro_context(macro_score=0.55, macro_direction='bullish',
                                cot_score=0.50, sentiment_score=0.1)

    def _unlock_bearish_macro(self, strat):
        """Inject two falling COT readings so all macro gates open for SELL."""
        strat.set_macro_context(macro_score=-0.50, macro_direction='bearish',
                                cot_score=-0.30, sentiment_score=-0.1)
        strat.set_macro_context(macro_score=-0.55, macro_direction='bearish',
                                cot_score=-0.50, sentiment_score=-0.1)

    def test_regime_detection_returns_valid(self, ohlcv_df):
        from advanced_strategy import AdvancedStrategy
        strat = AdvancedStrategy(backtest_mode=True)
        regime = strat.detect_market_regime(ohlcv_df)
        assert regime in ('trending', 'ranging', 'neutral')

    def test_neutral_macro_blocks_all_signals(self, ohlcv_df):
        """Default neutral macro must produce no signals — the primary safety gate."""
        from advanced_strategy import AdvancedStrategy
        strat = AdvancedStrategy(backtest_mode=True)
        # _macro_direction defaults to 'neutral', score=0.0 — no signals allowed
        for i in range(100, len(ohlcv_df)):
            result = strat.generate_signal(ohlcv_df.iloc[:i + 1].copy())
            assert result is None, "Neutral macro must never generate a signal"

    def test_signal_has_required_keys(self, ohlcv_df):
        """With bullish macro unlocked, signals must contain all required keys."""
        from advanced_strategy import AdvancedStrategy
        strat = AdvancedStrategy(backtest_mode=True)
        self._unlock_bullish_macro(strat)

        signal_found = False
        for i in range(100, len(ohlcv_df)):
            window = ohlcv_df.iloc[:i + 1].copy()
            signal = strat.generate_signal(window)
            if signal is not None:
                for key in ('type', 'price', 'sl', 'tp', 'atr', 'regime', 'confidence'):
                    assert key in signal, f"Signal missing key: {key}"
                assert signal['type'] in ('BUY', 'SELL')
                assert signal['atr'] > 0
                signal_found = True
                break

        # Also try bearish macro if bullish produced no signal with this data
        if not signal_found:
            strat2 = AdvancedStrategy(backtest_mode=True)
            self._unlock_bearish_macro(strat2)
            for i in range(100, len(ohlcv_df)):
                window = ohlcv_df.iloc[:i + 1].copy()
                signal = strat2.generate_signal(window)
                if signal is not None:
                    for key in ('type', 'price', 'sl', 'tp', 'atr', 'regime', 'confidence'):
                        assert key in signal, f"Signal missing key: {key}"
                    signal_found = True
                    break

        assert signal_found, (
            "No signal produced in 100 windows with macro unlocked — "
            "check pullback/EMA/delta logic"
        )

    def test_signal_sl_tp_direction(self, ohlcv_df):
        """SL must be on the losing side and TP on the winning side of entry."""
        from advanced_strategy import AdvancedStrategy
        strat = AdvancedStrategy(backtest_mode=True)
        self._unlock_bullish_macro(strat)

        signal_found = False
        for i in range(100, len(ohlcv_df)):
            window = ohlcv_df.iloc[:i + 1].copy()
            signal = strat.generate_signal(window)
            if signal is not None:
                if signal['type'] == 'BUY':
                    assert signal['sl'] < signal['price']
                    assert signal['tp'] > signal['price']
                else:
                    assert signal['sl'] > signal['price']
                    assert signal['tp'] < signal['price']
                signal_found = True
                break

        if not signal_found:
            strat2 = AdvancedStrategy(backtest_mode=True)
            self._unlock_bearish_macro(strat2)
            for i in range(100, len(ohlcv_df)):
                window = ohlcv_df.iloc[:i + 1].copy()
                signal = strat2.generate_signal(window)
                if signal is not None:
                    if signal['type'] == 'BUY':
                        assert signal['sl'] < signal['price']
                        assert signal['tp'] > signal['price']
                    else:
                        assert signal['sl'] > signal['price']
                        assert signal['tp'] < signal['price']
                    signal_found = True
                    break

        assert signal_found, "No signal to validate SL/TP direction — check strategy gates"

    def test_insufficient_data_returns_none(self):
        from advanced_strategy import AdvancedStrategy
        strat = AdvancedStrategy(backtest_mode=True)
        self._unlock_bullish_macro(strat)
        tiny_df = pd.DataFrame({
            'time': pd.date_range('2024-01-01', periods=10, freq='15min'),
            'open': [2000] * 10, 'high': [2001] * 10,
            'low': [1999] * 10, 'close': [2000] * 10,
            'tick_volume': [500] * 10,
        })
        assert strat.generate_signal(tiny_df) is None


# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------

class TestConfigValidation:

    def test_valid_config_passes(self, monkeypatch):
        monkeypatch.setenv('MT5_LOGIN', '12345')
        monkeypatch.setenv('MT5_PASSWORD', 'secret')
        monkeypatch.setenv('MT5_SERVER', 'HFM-Demo')
        # Reload config with patched env
        import importlib
        import config as cfg_module
        importlib.reload(cfg_module)
        # Should not raise
        cfg_module.validate_config()

    def test_missing_login_raises(self, monkeypatch):
        monkeypatch.setenv('MT5_LOGIN', '0')
        monkeypatch.setenv('MT5_PASSWORD', 'secret')
        monkeypatch.setenv('MT5_SERVER', 'HFM-Demo')
        import importlib
        import config as cfg_module
        importlib.reload(cfg_module)
        with pytest.raises(ValueError, match='MT5_LOGIN'):
            cfg_module.validate_config()

    def test_missing_password_raises(self, monkeypatch):
        monkeypatch.setenv('MT5_LOGIN', '12345')
        monkeypatch.setenv('MT5_PASSWORD', '')
        monkeypatch.setenv('MT5_SERVER', 'HFM-Demo')
        import importlib
        import config as cfg_module
        importlib.reload(cfg_module)
        with pytest.raises(ValueError, match='MT5_PASSWORD'):
            cfg_module.validate_config()


# ---------------------------------------------------------------------------
# MAEMFEAnalyzer tests
# ---------------------------------------------------------------------------

class TestMAEMFEAnalyzer:

    def _make_bars(self, n=50):
        rng = np.random.default_rng(7)
        close = 2000 + np.cumsum(rng.normal(0, 1, n))
        return pd.DataFrame({
            'time': pd.date_range('2024-01-01', periods=n, freq='15min'),
            'open': close,
            'high': close + rng.uniform(0.5, 2, n),
            'low': close - rng.uniform(0.5, 2, n),
            'close': close,
            'tick_volume': rng.integers(100, 500, n),
        })

    def test_price_path_with_close_ts(self):
        from mae_mfe_analyzer import MAEMFEAnalyzer
        analyzer = MAEMFEAnalyzer.__new__(MAEMFEAnalyzer)
        df = self._make_bars(50)
        df['time'] = pd.to_datetime(df['time'])
        ts_entry = df['time'].iloc[5]
        ts_close = df['time'].iloc[15]
        path = analyzer._price_path(df, ts_entry, ts_close)
        assert len(path) == 11  # bars 5..15 inclusive

    def test_price_path_no_close_ts(self):
        from mae_mfe_analyzer import MAEMFEAnalyzer
        analyzer = MAEMFEAnalyzer.__new__(MAEMFEAnalyzer)
        df = self._make_bars(50)
        df['time'] = pd.to_datetime(df['time'])
        ts_entry = df['time'].iloc[10]
        path = analyzer._price_path(df, ts_entry, None)
        assert len(path) == 20  # 20-bar fallback

    def test_price_path_no_match_returns_empty(self):
        from mae_mfe_analyzer import MAEMFEAnalyzer
        analyzer = MAEMFEAnalyzer.__new__(MAEMFEAnalyzer)
        df = self._make_bars(10)
        df['time'] = pd.to_datetime(df['time'])
        future_ts = pd.Timestamp('2099-01-01')
        path = analyzer._price_path(df, future_ts, None)
        assert path.empty

    def test_excursions_buy(self):
        from mae_mfe_analyzer import MAEMFEAnalyzer
        analyzer = MAEMFEAnalyzer.__new__(MAEMFEAnalyzer)
        df = self._make_bars(50)
        df['time'] = pd.to_datetime(df['time'])
        # Construct a fake trade row matching the column indices used in the code
        # t[1]=ts_entry, t[4]=type, t[5]=entry, t[6]=exit, t[10]=profit, t[13]=net, t[21]=close_ts
        trade = [None] * 22
        trade[1] = str(df['time'].iloc[5])
        trade[4] = 'BUY'
        trade[5] = float(df['close'].iloc[5])
        trade[6] = float(df['close'].iloc[15])
        trade[10] = 5.0
        trade[13] = 5.0
        trade[21] = str(df['time'].iloc[15])
        result = analyzer._excursions_for_trade(trade, df)
        assert result is not None
        mae, mfe, eff, is_win = result
        assert mae >= 0
        assert mfe >= 0
        assert is_win is True

    def test_build_result_keys(self):
        from mae_mfe_analyzer import MAEMFEAnalyzer
        analyzer = MAEMFEAnalyzer.__new__(MAEMFEAnalyzer)
        mae_list = [0.1, 0.2, 0.15]
        mfe_list = [0.3, 0.4, 0.35]
        eff_list = [80.0, 70.0, 75.0]
        result = analyzer._build_result(mae_list, mfe_list, eff_list,
                                         [0.1], [0.3], [0.2], [0.4])
        for key in ('trades_analysed', 'avg_mae_pct', 'avg_mfe_pct',
                    'avg_efficiency_pct', 'wins_analysed', 'losses_analysed',
                    'recommended_sl_pct', 'recommended_tp1_pct'):
            assert key in result

    def test_analyse_insufficient_trades(self, tmp_db):
        from mae_mfe_analyzer import MAEMFEAnalyzer
        analyzer = MAEMFEAnalyzer.__new__(MAEMFEAnalyzer)
        analyzer.db = tmp_db
        df = self._make_bars(50)
        result = analyzer.analyse(df, lookback_trades=100)
        assert result == {}


# ---------------------------------------------------------------------------
# PositionMonitor unit tests (no MT5 connection needed)
# ---------------------------------------------------------------------------

class TestPositionMonitor:

    def _make_monitor(self):
        from unittest.mock import MagicMock, patch
        import sys
        # MetaTrader5 is Windows-only; stub it out for Linux CI
        mt5_mock = MagicMock()
        mt5_mock.ORDER_TYPE_BUY = 0
        mt5_mock.ORDER_TYPE_SELL = 1
        sys.modules.setdefault('MetaTrader5', mt5_mock)
        from position_monitor import PositionMonitor
        connector = MagicMock()
        connector.get_positions.return_value = []
        return PositionMonitor(connector), connector

    def test_track_and_untrack(self):
        monitor, _ = self._make_monitor()
        monitor.track(101, atr=2.0)
        assert 101 in monitor.tracked
        assert monitor.tracked[101]['atr'] == 2.0
        monitor.untrack(101)
        assert 101 not in monitor.tracked

    def test_update_trailing_stops_skips_untracked(self):
        from unittest.mock import MagicMock, patch
        monitor, connector = self._make_monitor()
        import sys
        mt5_mock = sys.modules['MetaTrader5']
        pos = MagicMock()
        pos.ticket = 999  # not tracked
        connector.get_positions.return_value = [pos]
        with patch('position_monitor.Config') as mock_cfg:
            mock_cfg.ENABLE_TRAILING_STOP = True
            mock_cfg.SYMBOL = 'XAUUSD'
            monitor.update_trailing_stops()
        connector.modify_position_sl.assert_not_called()

    def test_should_time_stop_triggers(self):
        from unittest.mock import patch
        monitor, connector = self._make_monitor()
        monitor.track(200, atr=2.0)
        meta = monitor.tracked[200]
        meta['bars_open'] = 100
        with patch('position_monitor.Config') as mock_cfg:
            mock_cfg.TIME_STOP_BARS = 8
            connector.close_position.return_value = True
            triggered = monitor._should_time_stop(200, meta, atr=2.0, profit_pts=0.1)
        assert triggered is True
        assert 200 not in monitor.tracked

    def test_should_time_stop_no_trigger_when_profitable(self):
        from unittest.mock import patch
        monitor, connector = self._make_monitor()
        monitor.track(201, atr=2.0)
        meta = monitor.tracked[201]
        meta['bars_open'] = 100
        with patch('position_monitor.Config') as mock_cfg:
            mock_cfg.TIME_STOP_BARS = 8
            triggered = monitor._should_time_stop(201, meta, atr=2.0, profit_pts=1.5)
        assert triggered is False


# ---------------------------------------------------------------------------
# COTFetcher helper tests
# ---------------------------------------------------------------------------

class TestCOTFetcherHelpers:

    def _make_fetcher(self):
        from cot_fetcher import COTFetcher
        fetcher = COTFetcher.__new__(COTFetcher)
        fetcher._cache = {}
        return fetcher

    def test_filter_gold_rows_by_market_name(self):
        fetcher = self._make_fetcher()
        df = pd.DataFrame({
            'Market_and_Exchange_Names': ['GOLD - COMMODITY EXCHANGE INC.', 'SILVER - COMEX'],
            'val': [1, 2],
        })
        result = fetcher._filter_gold_rows(df)
        assert len(result) == 1
        assert result.iloc[0]['val'] == 1

    def test_filter_gold_rows_by_code(self):
        fetcher = self._make_fetcher()
        df = pd.DataFrame({
            'commodity_code': ['088691', '084691'],
            'val': [10, 20],
        })
        result = fetcher._filter_gold_rows(df)
        assert len(result) == 1
        assert result.iloc[0]['val'] == 10

    def test_find_date_col_yyyy(self):
        fetcher = self._make_fetcher()
        df = pd.DataFrame({'yyyy-mm-dd': [], 'other': []})
        assert fetcher._find_date_col(df) == 'yyyy-mm-dd'

    def test_find_date_col_report_date(self):
        fetcher = self._make_fetcher()
        df = pd.DataFrame({'report_date_as_of': [], 'other': []})
        assert fetcher._find_date_col(df) == 'report_date_as_of'

    def test_find_position_cols_prod(self):
        fetcher = self._make_fetcher()
        df = pd.DataFrame({
            'prod_merc_positions_long_all': [],
            'prod_merc_positions_short_all': [],
        })
        long_col, short_col = fetcher._find_position_cols(df)
        assert long_col == 'prod_merc_positions_long_all'
        assert short_col == 'prod_merc_positions_short_all'

    def test_find_position_cols_fallback_comm(self):
        fetcher = self._make_fetcher()
        df = pd.DataFrame({
            'comm_positions_long': [],
            'comm_positions_short': [],
        })
        long_col, short_col = fetcher._find_position_cols(df)
        assert long_col == 'comm_positions_long'
        assert short_col == 'comm_positions_short'

    def test_normalise_range(self):
        fetcher = self._make_fetcher()
        series = pd.Series([0.0, 50.0, 100.0])
        assert fetcher._normalise(0.0, series) == pytest.approx(-1.0)
        assert fetcher._normalise(100.0, series) == pytest.approx(1.0)
        assert fetcher._normalise(50.0, series) == pytest.approx(0.0)

    def test_normalise_flat_series(self):
        fetcher = self._make_fetcher()
        series = pd.Series([5.0, 5.0, 5.0])
        assert fetcher._normalise(5.0, series) == 0.0
