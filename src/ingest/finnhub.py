"""Finnhub 데이터 수집기.

수집 항목:
- EPS 서프라이즈 (실제 vs 컨센서스 추정치)
- 내부자 거래 (임원 매수/매도)
- 기관 투자자 보유 변화
- 뉴스 감성 점수
"""

from __future__ import annotations

import os
from typing import Any

import requests

_BASE = "https://finnhub.io/api/v1"
_TIMEOUT = 20


class FinnhubIngester:
    """Finnhub API로 EPS 서프라이즈·내부자 거래·감성 데이터를 수집한다."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY", "")

    def fetch(self, ticker: str) -> dict[str, Any]:
        """
        반환 구조:
        {
            "eps_surprises": [...],       # 최근 4분기 EPS 실제 vs 추정
            "insider_transactions": [...], # 최근 내부자 거래
            "sentiment": {...},           # 뉴스 감성 점수
            "recommendation": {...},      # 애널리스트 추천 집계
            "error": str | None,
        }
        """
        if not self.api_key:
            return {"error": "FINNHUB_API_KEY 없음"}

        result: dict[str, Any] = {}
        errors: list[str] = []

        # EPS 서프라이즈
        eps, err = self._fetch_eps_surprise(ticker)
        result["eps_surprises"] = eps
        if err:
            errors.append(f"EPS: {err}")

        # 내부자 거래
        insider, err = self._fetch_insider(ticker)
        result["insider_transactions"] = insider
        if err:
            errors.append(f"Insider: {err}")

        result["sentiment"] = {}  # 무료 플랜 미지원 (403) — 제거

        # 애널리스트 추천
        rec, err = self._fetch_recommendation(ticker)
        result["recommendation"] = rec
        if err:
            errors.append(f"Recommendation: {err}")


        result["error"] = "; ".join(errors) if errors else None
        return result

    # ── 내부 메서드 ──────────────────────────────────────────────────────────

    def _mask(self, text: str) -> str:
        if self.api_key and self.api_key in text:
            text = text.replace(self.api_key, "***")
        return text

    def _get(self, endpoint: str, params: dict) -> tuple[Any, str | None]:
        params["token"] = self.api_key
        try:
            resp = requests.get(f"{_BASE}{endpoint}", params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json(), None
        except Exception as e:
            return None, self._mask(str(e))

    def _fetch_eps_surprise(self, ticker: str) -> tuple[list, str | None]:
        """최근 4분기 EPS 실제 vs 컨센서스."""
        data, err = self._get("/stock/earnings", {"symbol": ticker, "limit": 4})
        if err or not data:
            return [], err
        result = []
        for item in data[:4]:
            actual = item.get("actual")
            estimate = item.get("estimate")
            surprise_pct = None
            if actual is not None and estimate and estimate != 0:
                surprise_pct = round((actual - estimate) / abs(estimate) * 100, 2)
            result.append({
                "period": item.get("period", ""),
                "actual_eps": actual,
                "estimated_eps": estimate,
                "surprise_pct": surprise_pct,
            })
        return result, None

    def _fetch_insider(self, ticker: str) -> tuple[list, str | None]:
        """최근 내부자 거래 (매수/매도 건수 및 금액 집계)."""
        data, err = self._get("/stock/insider-transactions", {"symbol": ticker})
        if err or not data:
            return [], err
        transactions = data.get("data", [])[:10]
        result = []
        for t in transactions:
            result.append({
                "name": t.get("name", ""),
                "share": t.get("share", 0),
                "change": t.get("change", 0),
                "transaction_date": t.get("transactionDate", ""),
                "transaction_price": t.get("transactionPrice"),
            })
        return result, None

    def _fetch_sentiment(self, ticker: str) -> tuple[dict, str | None]:
        """뉴스 감성 점수 (buzz + sentiment)."""
        data, err = self._get("/news-sentiment", {"symbol": ticker})
        if err or not data:
            return {}, err
        return {
            "buzz_articles_in_last_week": data.get("buzz", {}).get("articlesInLastWeek"),
            "buzz_weekly_average": data.get("buzz", {}).get("weeklyAverage"),
            "buzz_score": data.get("buzz", {}).get("buzz"),
            "company_news_score": data.get("companyNewsScore"),
            "sector_avg_bullish": data.get("sectorAverageBullishPercent"),
            "sentiment_bullish_percent": data.get("sentiment", {}).get("bullishPercent"),
            "sentiment_bearish_percent": data.get("sentiment", {}).get("bearishPercent"),
        }, None

    def _fetch_recommendation(self, ticker: str) -> tuple[dict, str | None]:
        """최신 애널리스트 추천 집계."""
        data, err = self._get("/stock/recommendation", {"symbol": ticker})
        if err or not data:
            return {}, err
        if not isinstance(data, list) or not data:
            return {}, None
        latest = data[0]
        return {
            "period": latest.get("period", ""),
            "strong_buy": latest.get("strongBuy", 0),
            "buy": latest.get("buy", 0),
            "hold": latest.get("hold", 0),
            "sell": latest.get("sell", 0),
            "strong_sell": latest.get("strongSell", 0),
        }, None
