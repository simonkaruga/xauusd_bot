"""
News Sentiment Engine
======================
Scores gold-relevant news headlines using keyword-weighted NLP.
No API key required — uses free RSS feeds from Reuters, FXStreet, Kitco.

Sentiment score: -1.0 (strongly bearish gold) to +1.0 (strongly bullish gold)

**This is a SLOW-SIGNAL macro bias, not an entry trigger.**

RSS feeds have multi-hour publication lag. By the time a headline appears in an
RSS feed, the price reaction has already occurred (gold moves within 100-500ms
of breaking news). Using this score for entry timing adds noise, not edge.

Correct use: as a 24-48hr directional bias modifier injected into the strategy's
confidence calculation — the same role as COT or FRED data. Never as a gate
that blocks or triggers individual entries.

Cache: 4 hours (consistent with macro_engine.py — both are daily-horizon signals)
"""

import os
import json
import re
import requests
import defusedxml.ElementTree as ET
from datetime import datetime, timedelta, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
from logger import logger

_CACHE_PATH = 'logs/sentiment_cache.json'
_CACHE_TTL_HOURS = 4

_RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.fxstreet.com/rss/news",
    "https://www.kitco.com/rss/kitconews.rss",
    "https://www.investing.com/rss/news_25.rss",  # gold news
]

# Weighted keyword lists — higher weight = stronger signal
_BULLISH_GOLD = {
    'rate cut': 2.0, 'rate cuts': 2.0, 'dovish': 1.8, 'pivot': 1.5,
    'inflation': 1.2, 'cpi': 1.0, 'safe haven': 1.5, 'geopolitical': 1.3,
    'war': 1.2, 'conflict': 1.0, 'crisis': 1.2, 'recession': 1.3,
    'weak dollar': 1.5, 'dollar falls': 1.3, 'fed pause': 1.8,
    'yield drop': 1.5, 'yields fall': 1.5, 'risk off': 1.3,
    'gold rally': 1.5, 'gold rises': 1.2, 'gold gains': 1.2,
    'uncertainty': 0.8, 'slowdown': 0.8, 'stagflation': 1.5,
    'debt ceiling': 1.0, 'banking crisis': 1.5, 'bank failure': 1.5,
}

_BEARISH_GOLD = {
    'rate hike': 2.0, 'rate hikes': 2.0, 'hawkish': 1.8, 'taper': 1.5,
    'quantitative tightening': 1.5, ' qt ': 1.2, 'strong dollar': 1.5,
    'dollar rises': 1.3, 'dollar gains': 1.2, 'risk on': 1.3,
    'yield rise': 1.5, 'yields rise': 1.5, 'yields climb': 1.3,
    'strong jobs': 1.3, 'strong economy': 1.2, 'economic growth': 1.0,
    'gold falls': 1.5, 'gold drops': 1.5, 'gold declines': 1.2,
    'sell gold': 1.5, 'gold selloff': 1.5, 'profit taking': 0.8,
    'nfp beat': 1.3, 'jobs beat': 1.2, 'gdp beat': 1.0,
}

# Only score headlines containing these gold-relevant terms
_GOLD_RELEVANT = {
    'gold', 'xau', 'precious metal', 'bullion', 'fed', 'federal reserve',
    'fomc', 'powell', 'inflation', 'cpi', 'pce', 'treasury', 'yield',
    'dollar', 'usd', 'rate', 'geopolit', 'war', 'crisis', 'recession',
}


class NewsSentiment:
    def __init__(self):
        self._cache = self._load_cache()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_sentiment_score(self) -> dict:
        """
        Returns:
          score      : -1.0 to +1.0
          direction  : 'bullish' | 'bearish' | 'neutral'
          headlines  : list of scored headlines (top 5)
          article_count : number of relevant articles scored
        """
        if self._is_cache_fresh():
            cached = self._cache.get('result')
            if cached:
                return cached

        headlines = self._fetch_headlines()
        scored = self._score_headlines(headlines)

        if not scored:
            result = {'score': 0.0, 'direction': 'neutral',
                      'headlines': [], 'article_count': 0}
            self._save_cache(result)
            return result

        # Weighted average — more recent headlines weighted higher
        total_weight = 0.0
        weighted_sum = 0.0
        for i, (headline, score, _) in enumerate(scored):
            # Recency weight: most recent = 1.0, oldest = 0.5
            recency = 1.0 - (i / max(len(scored), 1)) * 0.5
            weighted_sum += score * recency
            total_weight += recency

        raw_score = weighted_sum / total_weight if total_weight > 0 else 0.0
        final_score = max(-1.0, min(1.0, raw_score))

        if final_score >= 0.15:
            direction = 'bullish'
        elif final_score <= -0.15:
            direction = 'bearish'
        else:
            direction = 'neutral'

        result = {
            'score': round(final_score, 3),
            'direction': direction,
            'headlines': [(h, round(s, 3)) for h, s, _ in scored[:5]],
            'article_count': len(scored),
            'timestamp': _utcnow().isoformat(),
        }

        self._save_cache(result)
        logger.info(
            f"News Sentiment: score={final_score:+.3f} direction={direction} "
            f"({len(scored)} relevant articles)"
        )
        return result

    def get_size_multiplier(self, signal_type: str) -> float:
        """
        Returns a position-size multiplier (0.80–1.15) based on sentiment alignment.

        This is a SLOW bias modifier only — it never blocks an entry.
        RSS sentiment is a 24-48hr horizon signal; blocking intraday entries
        based on lagged headline data adds noise rather than edge.

        Range kept intentionally tight (±15%) to prevent sentiment from
        overriding the macro/technical signal hierarchy.
        """
        s = self.get_sentiment_score()
        score = s['score']

        if signal_type == 'BUY':
            if score >= 0.3:
                return min(1.15, 1.0 + score * 0.2)
            elif score <= -0.3:
                return max(0.80, 1.0 + score * 0.2)
        else:  # SELL
            if score <= -0.3:
                return min(1.15, 1.0 + abs(score) * 0.2)
            elif score >= 0.3:
                return max(0.80, 1.0 - score * 0.2)
        return 1.0

    # ------------------------------------------------------------------
    # RSS fetching
    # ------------------------------------------------------------------
    def _fetch_headlines(self) -> list:
        headlines = []
        for url in _RSS_FEEDS:
            try:
                resp = requests.get(url, timeout=8,
                                    headers={'User-Agent': 'Mozilla/5.0'})
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.content)
                for item in root.iter('item'):
                    title = item.findtext('title') or ''
                    desc = item.findtext('description') or ''
                    headlines.append(f"{title} {desc}".lower())
            except requests.exceptions.RequestException as e:
                logger.debug(f"RSS fetch failed {url}: {e}")
            except ET.ParseError as e:
                logger.debug(f"RSS parse error {url}: {e}")
        return headlines

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def _score_headlines(self, headlines: list) -> list:
        scored = []
        for headline in headlines:
            # Only process gold-relevant headlines
            if not any(term in headline for term in _GOLD_RELEVANT):
                continue

            bull_score = sum(
                weight for kw, weight in _BULLISH_GOLD.items()
                if kw in headline
            )
            bear_score = sum(
                weight for kw, weight in _BEARISH_GOLD.items()
                if kw in headline
            )

            if bull_score == 0 and bear_score == 0:
                continue

            total = bull_score + bear_score
            net = (bull_score - bear_score) / total if total > 0 else 0.0
            scored.append((headline[:120], net, total))

        # Sort by absolute signal strength
        scored.sort(key=lambda x: abs(x[1]) * x[2], reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------
    def _is_cache_fresh(self) -> bool:
        ts = self._cache.get('timestamp')
        if not ts:
            return False
        age_hours = (_utcnow() - datetime.fromisoformat(ts)).total_seconds() / 3600
        return age_hours < _CACHE_TTL_HOURS

    def _save_cache(self, result: dict):
        os.makedirs('logs', exist_ok=True)
        self._cache = {'timestamp': _utcnow().isoformat(), 'result': result}
        try:
            with open(_CACHE_PATH, 'w') as f:
                json.dump(self._cache, f)
        except Exception as e:
            logger.warning(f"Sentiment cache save failed: {e}")

    def _load_cache(self) -> dict:
        if os.path.exists(_CACHE_PATH):
            try:
                with open(_CACHE_PATH) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}
