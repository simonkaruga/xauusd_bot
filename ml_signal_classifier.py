"""
ML Signal Classifier
=====================
Filters trade signals using a Random Forest trained on historical bar features.

Feature set (20 features):
  EMA cross angle, RSI, ATR ratio, volume delta, ADX, Bollinger position,
  momentum, OHLC patterns, session encoding.

Usage:
  clf = MLSignalClassifier()
  clf.train(df_bars, trades_history)   # call after each backtest
  score = clf.predict(df_bars)          # 0.0-1.0 quality score
  if score > 0.6:                       # threshold = min acceptable quality
      place_trade()
"""

import os
import pickle
import numpy as np
import pandas as pd
import pandas_ta as ta
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import classification_report
from logger import logger


MODEL_PATH   = 'logs/signal_classifier.pkl'
SCALER_PATH  = 'logs/signal_scaler.pkl'
DRIFT_PATH   = 'logs/signal_drift_stats.pkl'
MIN_TRAINING_SAMPLES = 30
PREDICTION_THRESHOLD = 0.55

# PSI thresholds (Population Stability Index)
# Standard thresholds (large N) are 0.10/0.25.  With a 50-bar buffer and
# 10 buckets (5 samples/bucket on average) sampling variance alone produces
# PSI ≈ 0.18 on identical distributions, so we use wider thresholds that
# remain well below what real regime shifts produce (PSI > 1.0 typically).
# PSI < 0.25 → stable; 0.25-0.50 → monitor; > 0.50 → flag for retrain
PSI_MONITOR  = 0.25
PSI_CRITICAL = 0.50

# Rolling buffer: how many recent feature vectors to compare against training.
# Require at least DRIFT_MIN_SAMPLES before running the check.
DRIFT_BUFFER_SIZE  = 50
DRIFT_MIN_SAMPLES  = 40


class MLSignalClassifier:
    def __init__(self):
        self.model = None
        self.scaler = StandardScaler()
        self.is_trained = False
        self.feature_names = []
        self._cot_score = 0.0          # injected weekly from COTFetcher
        self._train_dist: dict = {}    # {feature: {'deciles': array(11,)}} saved at train time
        self._live_buffer: list = []   # rolling list of recent feature vectors (raw, pre-scale)
        self._drift_warned = False
        self._load()

    # ------------------------------------------------------------------
    # Feature engineering (same for training and live prediction)
    # ------------------------------------------------------------------
    def _extract_features(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # Trend
        df['ema9'] = ta.ema(df['close'], length=9)
        df['ema21'] = ta.ema(df['close'], length=21)
        df['ema50'] = ta.ema(df['close'], length=50)
        df['ema_cross_pct'] = (df['ema9'] - df['ema21']) / df['ema21'] * 100

        # Trend slope (angle of EMA9 over last 3 bars)
        df['ema9_slope'] = df['ema9'].diff(3) / df['close'] * 100

        # Momentum
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['rsi_slope'] = df['rsi'].diff(3)
        df['mom'] = df['close'].pct_change(5) * 100

        # Volatility
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['atr_ratio'] = df['atr'] / df['close'] * 100   # ATR as % of price
        df['atr_norm'] = df['atr'] / df['atr'].rolling(50).mean()  # vs average

        # Bollinger bands
        bbands = ta.bbands(df['close'], length=20)
        df['bb_upper'] = bbands['BBU_20_2.0']
        df['bb_lower'] = bbands['BBL_20_2.0']
        df['bb_mid'] = bbands['BBM_20_2.0']
        df['bb_pos'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid'] * 100

        # ADX (trend strength)
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        df['adx'] = adx['ADX_14']
        df['di_plus'] = adx['DMP_14']
        df['di_minus'] = adx['DMN_14']
        df['di_diff'] = df['di_plus'] - df['di_minus']

        # Volume delta (approximated)
        df['buy_vol'] = df['tick_volume'] * (df['close'] > df['open']).astype(float)
        df['sell_vol'] = df['tick_volume'] * (df['close'] <= df['open']).astype(float)
        df['delta_ratio'] = (df['buy_vol'] - df['sell_vol']) / (df['tick_volume'] + 1e-9)

        # OHLC patterns
        df['body_pct'] = abs(df['close'] - df['open']) / (df['high'] - df['low'] + 1e-9)
        df['upper_wick'] = (df['high'] - df[['open', 'close']].max(axis=1)) / (df['high'] - df['low'] + 1e-9)
        df['lower_wick'] = (df[['open', 'close']].min(axis=1) - df['low']) / (df['high'] - df['low'] + 1e-9)

        # Session (hour of day, 0-23)
        if 'time' in df.columns:
            df['hour'] = pd.to_datetime(df['time']).dt.hour
            df['session_london_ny'] = ((df['hour'] >= 12) & (df['hour'] < 16)).astype(float)
        else:
            df['hour'] = 12.0
            df['session_london_ny'] = 1.0

        # Price context: distance from recent high/low
        df['dist_from_high20'] = (df['high'].rolling(20).max() - df['close']) / df['close'] * 100
        df['dist_from_low20'] = (df['close'] - df['low'].rolling(20).min()) / df['close'] * 100

        # COT macro score — weekly, same value for all bars in a week
        # Injected externally via set_cot_score(); defaults to 0.0 (neutral)
        df['cot_score'] = getattr(self, '_cot_score', 0.0)

        feature_cols = [
            'ema_cross_pct', 'ema9_slope',
            'rsi', 'rsi_slope', 'mom',
            'atr_ratio', 'atr_norm',
            'bb_pos', 'bb_width',
            'adx', 'di_diff',
            'delta_ratio',
            'body_pct', 'upper_wick', 'lower_wick',
            'session_london_ny',
            'dist_from_high20', 'dist_from_low20',
            'cot_score',          # CFTC commercial net position: -1 to +1
        ]

        self.feature_names = feature_cols
        return df[feature_cols]

    # ------------------------------------------------------------------
    # Training: use backtest trade results as labels
    # ------------------------------------------------------------------
    def train(self, df: pd.DataFrame, trades: list) -> bool:
        """
        Train on completed trades from backtester.
        trades: list of dicts with keys: entry_bar, profit
        """
        if len(trades) < MIN_TRAINING_SAMPLES:
            logger.warning(f"ML Classifier: need {MIN_TRAINING_SAMPLES} trades, got {len(trades)}")
            return False

        features_df = self._extract_features(df)
        features_df = features_df.dropna()

        X_list, y_list = [], []

        for trade in trades:
            bar_idx = trade.get('entry_bar', None)
            if bar_idx is None or bar_idx >= len(features_df):
                continue
            row = features_df.iloc[bar_idx]
            if row.isna().any():
                continue
            X_list.append(row.values)
            y_list.append(1 if trade['profit'] > 0 else 0)

        if len(X_list) < MIN_TRAINING_SAMPLES:
            logger.warning("ML Classifier: not enough valid training rows after alignment")
            return False

        X = np.array(X_list)
        y = np.array(y_list)

        # Time-series cross-validation (no data leakage)
        tscv = TimeSeriesSplit(n_splits=3)
        scores = []

        for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
            X_tr, X_val = X[train_idx], X[val_idx]
            y_tr, y_val = y[train_idx], y[val_idx]
            scaler = StandardScaler()
            X_tr_sc = scaler.fit_transform(X_tr)
            X_val_sc = scaler.transform(X_val)
            clf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42,
                                         class_weight='balanced', n_jobs=-1)
            clf.fit(X_tr_sc, y_tr)
            score = clf.score(X_val_sc, y_val)
            scores.append(score)

        avg_cv_score = np.mean(scores)
        logger.info(f"ML Classifier CV accuracy: {avg_cv_score:.3f} (folds: {scores})")

        # Train final model on all data
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        self.model = GradientBoostingClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, random_state=42
        )
        self.model.fit(X_scaled, y)
        self.is_trained = True

        # Feature importance
        importances = self.model.feature_importances_
        top5 = sorted(zip(self.feature_names, importances), key=lambda x: -x[1])[:5]
        logger.info(f"Top 5 signal features: {[(n, round(v, 3)) for n, v in top5]}")

        # Save training distribution (deciles per feature) for PSI drift detection
        self._train_dist = {}
        for idx, name in enumerate(self.feature_names):
            col = X[:, idx]
            # 11 points → 10 equal-probability buckets (deciles)
            self._train_dist[name] = {
                'deciles': np.percentile(col, np.linspace(0, 100, 11)),
                'importance': float(importances[idx]),
            }
        self._live_buffer = []   # reset buffer on retrain
        self._drift_warned = False

        self._save()
        logger.info(f"ML Classifier trained on {len(X)} trades, saved to {MODEL_PATH}")
        return True

    # ------------------------------------------------------------------
    # Live prediction on latest bar window
    # ------------------------------------------------------------------
    def predict(self, df: pd.DataFrame, direction: str = 'BUY') -> float:
        """
        Returns probability score 0.0-1.0 that this signal will be profitable.
        direction: 'BUY' or 'SELL'
        """
        if not self.is_trained:
            return 0.6  # Default pass-through if no model yet

        try:
            features_df = self._extract_features(df)
            last_row = features_df.iloc[-1]

            if last_row.isna().any():
                return 0.6

            raw_vec = last_row.values

            # Buffer for drift detection (keep last DRIFT_BUFFER_SIZE vectors)
            self._live_buffer.append(raw_vec)
            if len(self._live_buffer) > DRIFT_BUFFER_SIZE:
                self._live_buffer.pop(0)

            # Run drift check every 20 predictions once buffer is warm enough
            if len(self._live_buffer) >= DRIFT_MIN_SAMPLES and len(self._live_buffer) % 20 == 0:
                self.check_feature_drift()

            X = raw_vec.reshape(1, -1)
            X_scaled = self.scaler.transform(X)
            proba = self.model.predict_proba(X_scaled)[0]
            win_prob = float(proba[1]) if len(proba) > 1 else 0.5

            logger.info(f"ML Signal Score: {win_prob:.3f} (threshold={PREDICTION_THRESHOLD})")
            return win_prob

        except Exception as e:
            logger.warning(f"ML prediction error: {e}")
            return 0.6

    # ------------------------------------------------------------------
    # Feature drift detection via Population Stability Index (PSI)
    #
    # PSI measures how much the distribution of each input feature has
    # shifted since the model was trained. If the market regime changes
    # (e.g., trending → range-bound) the feature distributions shift and
    # the model's predictions become unreliable even if it never errors.
    #
    # PSI = Σ (live_pct_i - train_pct_i) × ln(live_pct_i / train_pct_i)
    # Buckets: same decile boundaries as training distribution.
    # ------------------------------------------------------------------
    def check_feature_drift(self) -> dict:
        """
        Compare the live feature buffer against training distributions.
        Returns {feature: psi_score, ...} and logs warnings for drifted features.
        Called automatically every 20 predictions; also callable manually.
        """
        if not self._train_dist or len(self._live_buffer) < 10:
            return {}

        live_X = np.array(self._live_buffer)   # shape (N, n_features)
        psi_scores = {}
        critical_features = []
        monitor_features  = []

        for idx, name in enumerate(self.feature_names):
            if name not in self._train_dist:
                continue
            live_col  = live_X[:, idx]
            deciles   = self._train_dist[name]['deciles']
            importance = self._train_dist[name]['importance']

            psi = self._psi(live_col, deciles)
            psi_scores[name] = round(float(psi), 4)

            if psi >= PSI_CRITICAL and importance > 0.03:
                critical_features.append((name, psi, importance))
            elif psi >= PSI_MONITOR and importance > 0.03:
                monitor_features.append((name, psi, importance))

        if critical_features:
            self._drift_warned = True
            logger.warning(
                "FEATURE DRIFT CRITICAL — model may be stale. "
                "Drifted features (PSI≥%.2f): %s. Consider retraining.",
                PSI_CRITICAL,
                [(n, f"psi={p:.3f}", f"imp={i:.3f}") for n, p, i in critical_features]
            )
        elif monitor_features:
            logger.info(
                "Feature drift MONITOR (PSI %.2f-%.2f): %s",
                PSI_MONITOR, PSI_CRITICAL,
                [(n, f"psi={p:.3f}") for n, p in [(x[0], x[1]) for x in monitor_features]]
            )
        else:
            logger.info(
                "Feature drift check: stable (buffer=%d, max PSI=%.4f)",
                len(self._live_buffer),
                max(psi_scores.values()) if psi_scores else 0.0
            )

        return {
            'psi_scores': psi_scores,
            'critical': [(n, p) for n, p, _ in critical_features],
            'monitor':  [(n, p) for n, p, _ in monitor_features],
            'needs_retrain': len(critical_features) > 0,
        }

    def _psi(self, live_col: np.ndarray, train_deciles: np.ndarray) -> float:
        """
        Compute PSI for one feature.
        train_deciles: 11 values defining 10 equal-probability training buckets.
        """
        n_live  = len(live_col)
        n_buckets = len(train_deciles) - 1   # 10
        train_pct = 1.0 / n_buckets          # equal-probability by construction

        psi = 0.0
        for i in range(n_buckets):
            lo = train_deciles[i]
            hi = train_deciles[i + 1]
            if i == n_buckets - 1:
                live_count = np.sum((live_col >= lo) & (live_col <= hi))
            else:
                live_count = np.sum((live_col >= lo) & (live_col < hi))

            live_pct = live_count / n_live if n_live > 0 else 1e-9
            live_pct = max(live_pct, 1e-9)  # avoid log(0)

            psi += (live_pct - train_pct) * np.log(live_pct / train_pct)

        return float(psi)

    def set_cot_score(self, cot_score: float):
        """
        Inject the latest CFTC COT normalised score (-1 to +1).
        Call this once per week after COT data refreshes (Friday close).
        The score is baked into every feature vector until next update.
        """
        self._cot_score = float(cot_score)
        logger.info(f"ML Classifier: COT score updated to {cot_score:+.3f}")

    def should_trade(self, df: pd.DataFrame, direction: str = 'BUY') -> bool:
        score = self.predict(df, direction)
        return score >= PREDICTION_THRESHOLD

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _save(self):
        os.makedirs('logs', exist_ok=True)
        with open(MODEL_PATH, 'wb') as f:
            pickle.dump(self.model, f)
        with open(SCALER_PATH, 'wb') as f:
            pickle.dump(self.scaler, f)
        if self._train_dist:
            with open(DRIFT_PATH, 'wb') as f:
                pickle.dump({'train_dist': self._train_dist,
                             'feature_names': self.feature_names}, f)

    def _load(self):
        if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
            try:
                with open(MODEL_PATH, 'rb') as f:
                    self.model = pickle.load(f)
                with open(SCALER_PATH, 'rb') as f:
                    self.scaler = pickle.load(f)
                self.is_trained = True
                logger.info("ML Classifier: loaded saved model")
            except Exception as e:
                logger.warning(f"ML Classifier: could not load model: {e}")
                self.is_trained = False

        if os.path.exists(DRIFT_PATH):
            try:
                with open(DRIFT_PATH, 'rb') as f:
                    d = pickle.load(f)
                self._train_dist  = d.get('train_dist', {})
                self.feature_names = d.get('feature_names', self.feature_names)
                logger.info("ML Classifier: loaded drift stats (%d features)", len(self._train_dist))
            except Exception as e:
                logger.warning(f"ML Classifier: could not load drift stats: {e}")
