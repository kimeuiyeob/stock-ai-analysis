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
import time
from datetime import datetime
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
            if key == "cpi_yoy":
                val, date, err = self._fetch_yoy(series_id)
            else:
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

    def _get(self, params: dict, retries: int = 3) -> requests.Response:
        """5xx 에러 시 최대 retries회 재시도."""
        for attempt in range(retries):
            resp = requests.get(_BASE, params=params, timeout=_TIMEOUT)
            if resp.status_code < 500 or attempt == retries - 1:
                resp.raise_for_status()
                return resp
            time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("재시도 초과")

    def _fetch_latest(self, series_id: str) -> tuple[float | None, str | None, str | None]:
        """해당 시리즈의 가장 최근 유효값을 반환한다. 결측값(.) 건너뜀."""
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5,  # 최근 결측값 대비 버퍼
        }
        try:
            resp = self._get(params)
            obs = resp.json().get("observations", [])
            for o in obs:
                val_str = o.get("value", ".")
                if val_str not in (".", None, ""):
                    return float(val_str), o.get("date"), None
            return None, None, "유효 데이터 없음"
        except Exception as e:
            return None, None, str(e)

    def _fetch_yoy(self, series_id: str) -> tuple[float | None, str | None, str | None]:
        """전년 동월 대비 YoY 성장률을 계산한다. (CPIAUCSL 등 인덱스 시리즈용)"""
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 18,  # 결측값 대비 충분한 버퍼
        }
        try:
            resp = self._get(params)
            obs = resp.json().get("observations", [])
            valid = [
                (o["date"], float(o["value"]))
                for o in obs
                if o.get("value") not in (".", None, "")
            ]
            if len(valid) < 2:
                return None, None, "YoY 계산에 필요한 데이터 부족"

            current_date_str, current_val = valid[0]
            current_dt = datetime.strptime(current_date_str, "%Y-%m-%d")
            target_dt = current_dt.replace(year=current_dt.year - 1)

            # 1년 전 날짜와 가장 가까운 유효값 탐색 (±45일 허용)
            year_ago_val = None
            for date_str, val in valid[1:]:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                if abs((dt - target_dt).days) <= 45:
                    year_ago_val = val
                    break

            if year_ago_val is None:
                return None, None, "전년 동월 데이터 없음"
            if year_ago_val == 0:
                return None, None, "전년 값이 0"

            yoy = round((current_val / year_ago_val - 1) * 100, 2)
            return yoy, current_date_str, None
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
