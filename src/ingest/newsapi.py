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
            "articles": [...],      # 관련 뉴스 최대 10개
            "sentiment": {...},     # 감성 점수
            "top_sources": [...],   # 주요 언론사
            "error": str | None,
        }
        """
        if not self.api_key:
            return {"error": "NEWS_API_KEY 없음", "articles": [], "sentiment": {}}

        short_name = self._extract_short_name(company_name)
        query = self._build_query(ticker, short_name)
        from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

        try:
            resp = requests.get(
                _BASE,
                params={
                    "q": query,
                    "from": from_date,
                    "sortBy": "relevancy",
                    "language": "en",
                    "pageSize": 20,
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

        # 관련성 필터링: 제목 또는 설명에 티커·회사명 포함된 기사만 유지
        relevant = [a for a in parsed if self._is_relevant(a, ticker, short_name)]
        # 관련 기사가 3개 미만이면 원본 유지 (검색 결과 자체가 적은 경우)
        final_articles = relevant[:10] if len(relevant) >= 3 else parsed[:10]

        sentiment = self._analyze_sentiment(final_articles)
        top_sources = list({a["source"] for a in final_articles if a["source"]})[:5]

        return {
            "articles": final_articles,
            "sentiment": sentiment,
            "top_sources": top_sources,
            "error": None,
        }

    def _extract_short_name(self, company_name: str) -> str:
        """회사명에서 핵심 단어 추출. 예: 'NVIDIA Corporation' → 'NVIDIA'"""
        import re
        if not company_name:
            return ""
        cleaned = re.sub(
            r'\b(Inc\.?|Corp\.?|Corporation|Ltd\.?|LLC|Limited|Co\.?|Group|Holdings?|Technologies?|Systems?|International)\b',
            "", company_name, flags=re.IGNORECASE,
        ).strip().rstrip(",.")
        words = cleaned.split()
        return words[0] if words else ""

    def _build_query(self, ticker: str, short_name: str) -> str:
        """티커와 회사 핵심명을 OR로 결합한 쿼리 생성."""
        if short_name and short_name.lower() != ticker.lower():
            return f'"{ticker}" OR "{short_name}"'
        return f'"{ticker}"'

    def _is_relevant(self, article: dict, ticker: str, short_name: str) -> bool:
        """제목 또는 설명에 티커나 회사 핵심명이 포함돼 있는지 확인."""
        text = (article.get("title", "") + " " + article.get("description", "")).lower()
        if ticker.lower() in text:
            return True
        if short_name and short_name.lower() in text:
            return True
        return False

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
