"""Alpha Vantage 데이터 수집기.

수집 항목:
- EPS 컨센서스 추정치 (연간/분기)
- 재무제표 심화 (연간 손익계산서)
- 기업 개요 (섹터, 업종, 시총, 배당수익률 등)
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

_BASE = "https://www.alphavantage.co/query"
_TIMEOUT = 15


class AlphaVantageIngester:
    """Alpha Vantage API로 EPS 추정치 및 심화 재무 데이터를 수집한다."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("ALPHA_VANTAGE_API_KEY", "")

    def fetch(self, ticker: str) -> dict[str, Any]:
        """
        반환 구조:
        {
            "overview": {...},           # 기업 개요 (배당수익률, 52주 목표가 등)
            "earnings": {...},           # EPS 실제 + 추정치
            "annual_income": [...],      # 연간 손익계산서 (최근 3년)
            "error": str | None,
        }
        """
        if not self.api_key:
            return {"error": "ALPHA_VANTAGE_API_KEY 없음"}

        errors: list[str] = []
        result: dict[str, Any] = {}

        overview, err = self._fetch("OVERVIEW", ticker)
        if err:
            errors.append(f"Overview: {err}")
            result["overview"] = {}
        else:
            result["overview"] = self._parse_overview(overview or {})

        time.sleep(13)  # 무료 티어: 5req/min → 12초 간격

        earnings, err = self._fetch("EARNINGS", ticker)
        if err:
            errors.append(f"Earnings: {err}")
            result["earnings"] = {}
        else:
            result["earnings"] = self._parse_earnings(earnings or {})

        time.sleep(13)

        income, err = self._fetch("INCOME_STATEMENT", ticker)
        if err:
            errors.append(f"Income: {err}")
            result["annual_income"] = []
        else:
            result["annual_income"] = self._parse_annual_income(income or {})

        result["error"] = "; ".join(errors) if errors else None
        return result

    # ── 내부 메서드 ──────────────────────────────────────────────────────────

    def _mask(self, text: str) -> str:
        """에러 메시지에서 API 키를 마스킹한다."""
        if self.api_key and self.api_key in text:
            text = text.replace(self.api_key, "***")
        return text

    def _fetch(self, function: str, ticker: str) -> tuple[dict | None, str | None]:
        try:
            resp = requests.get(
                _BASE,
                params={"function": function, "symbol": ticker, "apikey": self.api_key},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            # API 한도 초과 메시지 처리
            if "Note" in data or "Information" in data:
                msg = data.get("Note") or data.get("Information", "API 한도 초과")
                return None, self._mask(msg)
            return data, None
        except Exception as e:
            return None, self._mask(str(e))

    def _parse_overview(self, data: dict) -> dict:
        return {
            "analyst_target_price": data.get("AnalystTargetPrice"),
            "dividend_yield": data.get("DividendYield"),
            "eps": data.get("EPS"),
            "revenue_per_share_ttm": data.get("RevenuePerShareTTM"),
            "profit_margin": data.get("ProfitMargin"),
            "beta": data.get("Beta"),
            "52_week_high": data.get("52WeekHigh"),
            "52_week_low": data.get("52WeekLow"),
            "50_day_ma": data.get("50DayMovingAverage"),
            "200_day_ma": data.get("200DayMovingAverage"),
            "forward_pe": data.get("ForwardPE"),
            "peg_ratio": data.get("PEGRatio"),
        }

    def _parse_earnings(self, data: dict) -> dict:
        annual = data.get("annualEarnings", [])[:3]
        quarterly = data.get("quarterlyEarnings", [])[:4]
        return {
            "annual_eps": [
                {"fiscal_year": e.get("fiscalDateEnding", ""), "eps": e.get("reportedEPS")}
                for e in annual
            ],
            "quarterly_eps": [
                {
                    "period": e.get("fiscalDateEnding", ""),
                    "actual_eps": e.get("reportedEPS"),
                    "estimated_eps": e.get("estimatedEPS"),
                    "surprise_pct": e.get("surprisePercentage"),
                }
                for e in quarterly
            ],
        }

    def _parse_annual_income(self, data: dict) -> list:
        reports = data.get("annualReports", [])[:3]
        result = []
        for r in reports:
            result.append({
                "fiscal_year": r.get("fiscalDateEnding", ""),
                "revenue": r.get("totalRevenue"),
                "gross_profit": r.get("grossProfit"),
                "operating_income": r.get("operatingIncome"),
                "net_income": r.get("netIncome"),
                "ebitda": r.get("ebitda"),
                "rd_expense": r.get("researchAndDevelopment"),
            })
        return result
