"""NewsAPI 뉴스 수집기.

수집 항목:
- 다양한 언론사 최신 뉴스 (Reuters, Bloomberg, WSJ 등)
- 긍정/부정/중립 감성 키워드 분석
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

_BASE = "https://newsapi.org/v2/everything"
_TIMEOUT = 10


class NewsApiIngester:
    """NewsAPI로 종목 관련 뉴스를 수집하고 감성을 분석한다."""

    POS_KW = {
        "beats", "surges", "growth", "strong", "record", "upgrade",
        "buy", "rally", "profit", "gains", "rises", "outperform",
        "bullish", "exceeded", "raised", "positive",
    }
    NEG_KW = {
        "miss", "decline", "loss", "cut", "downgrade", "sell", "warn",
        "risk", "drop", "falls", "lawsuit", "investigation", "bearish",
        "layoffs", "recall", "deficit", "disappoints", "below",
    }

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("NEWS_API_KEY", "")

    def fetch(self, ticker: str, company_name: str = "", days: int = 7) -> dict[str, Any]:
        """
        반환 구조:
        {
            "articles": [...],      # 최근 뉴스 최대 10개
            "sentiment": {...},     # 감성 점수
            "top_sources": [...],   # 주요 언론사
            "error": str | None,
        }
        """
        if not self.api_key:
            return {"error": "NEWS_API_KEY 없음", "articles": [], "sentiment": {}}

        query = company_name if company_name else ticker
        from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

        try:
            resp = requests.get(
                _BASE,
                params={
                    "q": query,
                    "from": from_date,
                    "sortBy": "relevancy",
                    "language": "en",
                    "pageSize": 10,
                    "apiKey": self.api_key,
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return {"error": str(e), "articles": [], "sentiment": {}}

        articles = data.get("articles", [])
        parsed = []
        for a in articles:
            parsed.append({
                "title": a.get("title", ""),
                "source": a.get("source", {}).get("name", ""),
                "published": a.get("publishedAt", "")[:10],
                "url": a.get("url", ""),
                "description": (a.get("description") or "")[:200],
            })

        sentiment = self._analyze_sentiment(parsed)
        top_sources = list({a["source"] for a in parsed if a["source"]})[:5]

        return {
            "articles": parsed,
            "sentiment": sentiment,
            "top_sources": top_sources,
            "error": None,
        }

    def _analyze_sentiment(self, articles: list[dict]) -> dict[str, Any]:
        pos = neg = 0
        pos_hits: list[str] = []
        neg_hits: list[str] = []

        for a in articles:
            text = (a.get("title", "") + " " + a.get("description", "")).lower()
            words = set(text.replace(",", " ").replace(".", " ").split())
            hp = words & self.POS_KW
            hn = words & self.NEG_KW
            if hp:
                pos += 1
                pos_hits.extend(hp)
            if hn:
                neg += 1
                neg_hits.extend(hn)

        total = len(articles)
        neutral = max(0, total - pos - neg)
        score = round((pos - neg) / total, 2) if total else 0.0

        return {
            "positive": pos,
            "negative": neg,
            "neutral": neutral,
            "score": score,          # -1(최악) ~ +1(최고)
            "pos_keywords": list(dict.fromkeys(pos_hits))[:5],
            "neg_keywords": list(dict.fromkeys(neg_hits))[:5],
        }
