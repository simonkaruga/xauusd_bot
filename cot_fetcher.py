"""
CFTC Commitment of Traders (COT) Fetcher
==========================================
Downloads the CFTC Disaggregated Futures COT report and extracts the
gold commercial (Producer/Merchant/Processor/User) net position.

Why this matters for XAU/USD:
  Commercials = mining companies, refiners, jewellers hedging REAL gold exposure.
  They are the most informed participants in the gold market.
  When commercials are net LONG gold (unusual for hedgers), it signals
  undervaluation — prices tend to rise.
  When commercials are net SHORT (normal hedging), it's neutral/bearish.

Score: -1.0 (max short) to +1.0 (max long) vs 52-week range.

Data source: CFTC public data, updated every Friday ~3:30 PM ET.
URL: https://www.cftc.gov/files/dea/history/fut_disagg_txtonly_{YEAR}.zip
Gold commodity code: "GOLD - COMMODITY EXCHANGE INC." (088691)

Cache: logs/cot_cache.json  (refreshed weekly)
"""

import os
import io
import json
import zipfile
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from logger import logger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

CACHE_PATH = 'logs/cot_cache.json'
GOLD_MARKET_NAME = 'GOLD - COMMODITY EXCHANGE INC.'
CACHE_TTL_HOURS = 168   # 1 week


class COTFetcher:
    def __init__(self):
        os.makedirs('logs', exist_ok=True)
        self._cache = self._load_cache()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_gold_cot_score(self) -> float:
        """
        Returns a normalised score -1.0 to +1.0:
          +1.0 = commercials at 52-week maximum long (bullish for gold)
          -1.0 = commercials at 52-week maximum short (bearish for gold)
           0.0 = no data / neutral
        """
        data = self._get_data()
        if data is None or len(data) < 2:
            logger.warning("COT: no data available — returning neutral score 0.0")
            return 0.0

        latest = data.iloc[-1]
        net_pos = latest['commercial_net']
        score = self._normalise(net_pos, data['commercial_net'])

        logger.info(
            f"COT Gold: commercial net={net_pos:+,.0f} contracts | "
            f"score={score:+.3f} | as of {latest['date']}"
        )
        return round(float(score), 3)

    def get_full_report(self) -> dict:
        """Return latest COT figures as a dict (for ML features)."""
        data = self._get_data()
        if data is None or data.empty:
            return {'cot_score': 0.0, 'cot_net': 0, 'cot_change_week': 0}

        latest = data.iloc[-1]
        prev = data.iloc[-2] if len(data) > 1 else latest
        net = float(latest['commercial_net'])
        score = self._normalise(net, data['commercial_net'])
        change = float(latest['commercial_net'] - prev['commercial_net'])

        return {
            'cot_score': round(float(score), 3),
            'cot_net': int(net),
            'cot_change_week': int(change),
            'cot_date': str(latest['date']),
        }

    # ------------------------------------------------------------------
    # Data retrieval with weekly cache
    # ------------------------------------------------------------------
    def _get_data(self) -> pd.DataFrame | None:
        if self._is_cache_fresh():
            return self._df_from_cache()

        for year in [_utcnow().year, _utcnow().year - 1]:
            df = self._download_year(year)
            if df is not None and not df.empty:
                self._save_cache(df)
                return df

        # Last resort: use stale cache
        if self._cache.get('rows'):
            logger.warning("COT: using stale cache (download failed)")
            return self._df_from_cache()

        return None

    # CFTC URL patterns to try in order (they change periodically)
    _URL_PATTERNS = [
        "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip",
        "https://www.cftc.gov/files/dea/history/fut_disagg_txtonly_{year}.zip",
        "https://www.cftc.gov/sites/default/files/files/dea/history/fut_disagg_txt_{year}.zip",
        "https://www.cftc.gov/sites/default/files/files/dea/history/fut_disagg_txtonly_{year}.zip",
        # Current-week reports (no year suffix)
        "https://www.cftc.gov/files/dea/history/fut_disagg_txt.zip",
        "https://www.cftc.gov/files/dea/history/com_disagg_txt.zip",
    ]

    def _download_year(self, year: int) -> pd.DataFrame | None:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; COT-fetcher/1.0)'}
        tried = []

        for pattern in self._URL_PATTERNS:
            url = pattern.format(year=year)
            if url in tried:
                continue
            tried.append(url)
            try:
                logger.info(f"COT: trying {url}")
                resp = requests.get(url, timeout=30, headers=headers)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()

                content_type = resp.headers.get('Content-Type', '')
                if 'zip' in content_type or resp.content[:4] == b'PK\x03\x04':
                    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                        names = [n for n in z.namelist()
                                 if n.lower().endswith(('.txt', '.csv'))]
                        if not names:
                            continue
                        raw = z.read(names[0]).decode('latin-1', errors='replace')
                else:
                    raw = resp.content.decode('latin-1', errors='replace')

                df = self._parse_disaggregated(raw)
                if df is not None and not df.empty:
                    logger.info(f"COT: parsed {len(df)} gold rows from {url}")
                    return df

            except requests.exceptions.RequestException as e:
                logger.debug(f"COT URL failed {url}: {e}")
            except Exception as e:
                logger.debug(f"COT parse error {url}: {e}")

        logger.warning(f"COT: all URL patterns failed for year={year}")
        return None

    # ------------------------------------------------------------------
    # Parser for CFTC disaggregated futures text/CSV
    # ------------------------------------------------------------------
    def _parse_disaggregated(self, raw: str) -> pd.DataFrame | None:
        try:
            df = pd.read_csv(io.StringIO(raw), low_memory=False)
        except Exception as e:
            logger.warning(f"COT CSV parse error: {e}")
            return None

        df.columns = [c.strip().replace('"', '') for c in df.columns]
        df = self._filter_gold_rows(df)
        if df is None or df.empty:
            return None

        date_col = self._find_date_col(df)
        long_col, short_col = self._find_position_cols(df)
        if not long_col or not short_col:
            return None

        result = pd.DataFrame()
        result['date'] = pd.to_datetime(df[date_col], errors='coerce') if date_col else pd.NaT
        result['commercial_long'] = pd.to_numeric(
            df[long_col].astype(str).str.replace(',', ''), errors='coerce'
        )
        result['commercial_short'] = pd.to_numeric(
            df[short_col].astype(str).str.replace(',', ''), errors='coerce'
        )
        result = result.dropna(subset=['commercial_long', 'commercial_short'])
        result['commercial_net'] = result['commercial_long'] - result['commercial_short']
        return result.sort_values('date').reset_index(drop=True)

    def _filter_gold_rows(self, df: pd.DataFrame) -> pd.DataFrame | None:
        market_col = next(
            (c for c in df.columns if 'market' in c.lower() and 'name' in c.lower()), None
        )
        if market_col:
            return df[df[market_col].str.upper().str.contains('GOLD', na=False)]
        code_col = next((c for c in df.columns if 'code' in c.lower()), None)
        if code_col:
            return df[df[code_col].astype(str).str.contains('088691', na=False)]
        logger.warning("COT: could not identify market column")
        return None

    def _find_date_col(self, df: pd.DataFrame) -> str | None:
        return next(
            (c for c in df.columns if 'yyyy' in c.lower() or 'yyyy-mm-dd' in c.lower()), None
        ) or next(
            (c for c in df.columns if any(k in c.lower() for k in ['report_date', 'as_of', 'date'])), None
        )

    def _find_position_cols(self, df: pd.DataFrame) -> tuple[str | None, str | None]:
        long_col = next(
            (c for c in df.columns if 'prod' in c.lower() and 'long' in c.lower() and 'all' in c.lower()), None
        )
        short_col = next(
            (c for c in df.columns if 'prod' in c.lower() and 'short' in c.lower() and 'all' in c.lower()), None
        )
        if not long_col or not short_col:
            long_col = next((c for c in df.columns if 'comm' in c.lower() and 'long' in c.lower()), None)
            short_col = next((c for c in df.columns if 'comm' in c.lower() and 'short' in c.lower()), None)
        if not long_col or not short_col:
            logger.warning(f"COT: cannot find commercial long/short columns. Available: {list(df.columns[:20])}")
        return long_col, short_col

    # ------------------------------------------------------------------
    # Normalise to -1..+1 vs 52-week range
    # ------------------------------------------------------------------
    def _normalise(self, value: float, series: pd.Series) -> float:
        last_52 = series.tail(52)   # ~52 weekly reports = 1 year
        mn, mx = float(last_52.min()), float(last_52.max())
        if mx == mn:
            return 0.0
        return (value - mn) / (mx - mn) * 2 - 1   # maps [min, max] → [-1, +1]

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------
    def _is_cache_fresh(self) -> bool:
        ts = self._cache.get('timestamp')
        if not ts:
            return False
        age_hours = (_utcnow() - datetime.fromisoformat(ts)).total_seconds() / 3600
        return age_hours < CACHE_TTL_HOURS

    def _save_cache(self, df: pd.DataFrame):
        rows = []
        for _, row in df.iterrows():
            rows.append({
                'date': str(row['date']),
                'commercial_long': float(row['commercial_long']),
                'commercial_short': float(row['commercial_short']),
                'commercial_net': float(row['commercial_net']),
            })
        self._cache = {'timestamp': _utcnow().isoformat(), 'rows': rows}
        with open(CACHE_PATH, 'w') as f:
            json.dump(self._cache, f)

    def _load_cache(self) -> dict:
        if os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH) as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def _df_from_cache(self) -> pd.DataFrame | None:
        rows = self._cache.get('rows')
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df['date'] = pd.to_datetime(df['date'])
        df['commercial_net'] = df['commercial_net'].astype(float)
        return df


# ------------------------------------------------------------------
# Standalone test
# ------------------------------------------------------------------
if __name__ == '__main__':
    fetcher = COTFetcher()
    report = fetcher.get_full_report()
    print(f"COT Score  : {report['cot_score']:+.3f}  (−1=max short, +1=max long)")
    print(f"Net Contracts: {report['cot_net']:+,}")
    print(f"Week Change  : {report['cot_change_week']:+,}")
    print(f"As of        : {report['cot_date']}")
