"""FRED (연방준비은행) 거시경제 데이터 수집기.

수집 항목:
- 기준금리 (FEDFUNDS)
- 10년물 국채금리 (DGS10)
- CPI 인플레이션 YoY (CPIAUCSL)
- 실업률 (UNRATE)
- GDP 성장률 (A191RL1Q225SBEA)
- 달러 인덱스 (DTWEXBGS)
"""

from __future__ import annotations

import os
from typing import Any

import requests

_BASE = "https://api.stlouisfed.org/fred/series/observations"
_TIMEOUT = 10


class FredIngester:
    """FRED API로 거시경제 지표를 수집한다."""

    SERIES = {
        "fed_funds_rate":   ("FEDFUNDS",       "기준금리 (%)"),
        "treasury_10y":     ("DGS10",          "미국 10년물 국채금리 (%)"),
        "cpi_yoy":          ("CPIAUCSL",       "CPI 인플레이션 YoY (%)"),
        "unemployment":     ("UNRATE",         "실업률 (%)"),
        "gdp_growth":       ("A191RL1Q225SBEA","실질 GDP 성장률 (%)"),
        "dollar_index":     ("DTWEXBGS",       "달러 인덱스"),
    }

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("FRED_API_KEY", "")

    def fetch(self) -> dict[str, Any]:
        """
        반환 구조:
        {
            "fed_funds_rate":  {"value": 5.33, "date": "2024-08-01", "label": "기준금리 (%)"},
            "treasury_10y":    {...},
            ...
            "macro_summary":   str,   # LLM 프롬프트용 한줄 요약
            "error": str | None,
        }
        """
        if not self.api_key:
            return {"error": "FRED_API_KEY 없음", "macro_summary": ""}

        results: dict[str, Any] = {}
        errors: list[str] = []

        for key, (series_id, label) in self.SERIES.items():
            val, date, err = self._fetch_latest(series_id)
            if err:
                errors.append(f"{series_id}: {err}")
                results[key] = {"value": None, "date": None, "label": label}
            else:
                results[key] = {"value": val, "date": date, "label": label}

        results["macro_summary"] = self._build_summary(results)
        results["error"] = "; ".join(errors) if errors else None
        return results

    # ── 내부 메서드 ──────────────────────────────────────────────────────────

    def _fetch_latest(self, series_id: str) -> tuple[float | None, str | None, str | None]:
        """해당 시리즈의 가장 최근 값을 반환한다."""
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1,
        }
        try:
            resp = requests.get(_BASE, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            obs = resp.json().get("observations", [])
            if not obs:
                return None, None, "데이터 없음"
            latest = obs[0]
            val_str = latest.get("value", ".")
            if val_str == ".":
                return None, latest.get("date"), "결측값"
            return float(val_str), latest.get("date"), None
        except Exception as e:
            return None, None, str(e)

    def _build_summary(self, results: dict[str, Any]) -> str:
        """리포트 프롬프트에 삽입할 거시경제 한줄 요약을 생성한다."""
        parts: list[str] = []

        def fmt(key: str, unit: str = "%") -> str:
            item = results.get(key, {})
            v = item.get("value")
            d = item.get("date", "")
            if v is None:
                return ""
            return f"{item['label']} {v}{unit} ({d})"

        for key in self.SERIES:
            line = fmt(key)
            if line:
                parts.append(line)

        return " | ".join(parts) if parts else "거시 데이터 수집 실패"
