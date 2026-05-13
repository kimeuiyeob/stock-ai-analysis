"""SEC EDGAR 수집기 — 가입/API 키 불필요.

10-K (연간보고서)에서 공식 리스크 팩터(Item 1A)를 추출한다.
"""

from __future__ import annotations

import re
import time
from typing import Any

import requests

# SEC EDGAR는 User-Agent 헤더를 필수로 요구 (형식: 앱명 이메일)
_HEADERS = {"User-Agent": "financial-ai-pipeline research@financial-ai.local"}
_TIMEOUT = 15


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp


class EdgarIngester:
    """SEC EDGAR에서 10-K 리스크 팩터를 수집한다."""

    TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

    def fetch(self, ticker: str) -> dict[str, Any]:
        """
        반환 구조:
        {
            "cik": str,
            "company_name": str,
            "latest_10k_date": str,
            "risk_factors": [str, ...],   # 핵심 리스크 문장 최대 10개
            "risk_factors_raw": str,      # 원문 요약 (최대 3000자)
            "error": str | None,
        }
        """
        try:
            cik, company_name = self._resolve_cik(ticker)
            if not cik:
                return self._empty(ticker, "CIK를 찾을 수 없습니다")

            filings = self._get_submissions(cik)
            accession, filing_date, primary_doc = self._latest_10k(filings)
            if not accession:
                return self._empty(ticker, "10-K 공시를 찾을 수 없습니다")

            raw_text = self._fetch_filing_text(cik, accession, primary_doc)
            if not raw_text:
                return self._empty(ticker, "10-K 문서 다운로드 실패")

            risk_raw = self._extract_risk_section(raw_text)
            risk_bullets = self._parse_risk_bullets(risk_raw)

            return {
                "cik": cik,
                "company_name": company_name,
                "latest_10k_date": filing_date,
                "risk_factors": risk_bullets,
                "risk_factors_raw": risk_raw[:3000] if risk_raw else "",
                "error": None,
            }
        except Exception as e:
            return self._empty(ticker, str(e))

    # ── 내부 메서드 ──────────────────────────────────────────────────────────

    def _resolve_cik(self, ticker: str) -> tuple[str | None, str]:
        """SEC 전체 티커 맵에서 CIK를 조회한다."""
        data = _get(self.TICKER_CIK_URL).json()
        ticker_upper = ticker.upper().split(".")[0]  # 005930.KS → 005930
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                cik = str(entry["cik_str"]).zfill(10)
                return cik, entry.get("title", ticker)
        return None, ticker

    def _get_submissions(self, cik: str) -> dict:
        url = self.SUBMISSIONS_URL.format(cik=cik)
        return _get(url).json()

    def _latest_10k(self, filings: dict) -> tuple[str | None, str, str]:
        """가장 최근 10-K의 (accession_no_dashes, date, primaryDocument)를 반환한다."""
        recent = filings.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])
        for form, acc, date, primary in zip(forms, accessions, dates, primary_docs):
            if form == "10-K":
                # acc 형식: "0000320193-25-000079" → 폴더명: "000032019325000079"
                return acc.replace("-", ""), date, primary
        return None, "", ""

    def _fetch_filing_text(self, cik: str, accession_no: str, primary_doc: str) -> str | None:
        """10-K 본문 파일을 직접 가져온다 (submissions JSON의 primaryDocument 활용)."""
        cik_int = str(int(cik))  # 앞자리 0 제거
        time.sleep(0.5)  # SEC 서버 부하 방지
        doc_url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik_int}"
            f"/{accession_no}/{primary_doc}"
        )
        try:
            return _get(doc_url).text
        except Exception:
            return None

    def _extract_risk_section(self, text: str) -> str:
        """10-K 본문에서 Item 1A (Risk Factors) 섹션을 추출한다."""
        # HTML 태그 제거
        clean = re.sub(r"<[^>]+>", " ", text)
        # 숫자형 엔티티 포함 모든 HTML 엔티티 제거 (&#160; &#8220; 등)
        clean = re.sub(r"&#?\w+;", " ", clean)
        clean = re.sub(r"\s+", " ", clean)

        # 목차(TOC)의 첫 번째 등장은 건너뛰고, 두 번째(본문) 등장부터 추출
        # Item 1A ~ Item 1B / Item 1C / Item 2 사이 추출
        end_markers = r"Item\s+1[B-Z]|Item\s+2|ITEM\s+1[B-Z]|ITEM\s+2"
        pattern = r"Item\s+1A[\.\s]+Risk\s+Factors(.*?)(?=" + end_markers + r")"

        matches = list(re.finditer(pattern, clean, re.IGNORECASE | re.DOTALL))
        if len(matches) >= 2:
            # 두 번째 매치가 본문 (첫 번째는 목차)
            section = matches[1].group(1).strip()
        elif len(matches) == 1:
            section = matches[0].group(1).strip()
        else:
            return ""

        # 너무 짧으면 목차만 잡힌 것
        if len(section) < 200:
            return ""

        return section[:5000]  # 최대 5000자

    def _parse_risk_bullets(self, risk_text: str) -> list[str]:
        """리스크 원문에서 핵심 문장을 최대 10개 추출한다."""
        if not risk_text:
            return []

        # 문장 단위로 분리
        sentences = re.split(r"(?<=[.!?])\s+", risk_text)

        # 리스크 관련 키워드가 포함된 문장 우선 선별
        risk_keywords = {
            "risk", "competition", "regulation", "interest rate", "economic",
            "cybersecurity", "litigation", "foreign", "supply chain",
            "uncertainty", "decline", "adverse", "failure", "loss",
        }
        selected: list[str] = []
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 30 or len(sent) > 400:
                continue
            lower = sent.lower()
            if any(kw in lower for kw in risk_keywords):
                selected.append(sent)
            if len(selected) >= 10:
                break

        # 키워드 매칭이 적으면 앞에서부터 추출
        if len(selected) < 3:
            selected = [
                s.strip() for s in sentences
                if 30 <= len(s.strip()) <= 400
            ][:10]

        return selected

    @staticmethod
    def _empty(ticker: str, error: str) -> dict[str, Any]:
        return {
            "cik": None,
            "company_name": ticker,
            "latest_10k_date": "",
            "risk_factors": [],
            "risk_factors_raw": "",
            "error": error,
        }
