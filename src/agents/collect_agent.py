"""CollectAgent — 6개 소스 데이터 수집 및 raw/ 분리 저장.

수집 전략:
  1단계) yfinance 단독 실행 — company_name 확보 + 파이프라인 중단 여부 판단
  2단계) 나머지 5개 소스 병렬 실행 (ThreadPoolExecutor)
  3단계) 전체 완료 후 snapshot.json 합본 저장 → 다음 Agent 진행
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from fio.storage import write_json
from ingest.yahoo import YahooIngester
from ingest.edgar import EdgarIngester
from ingest.fred import FredIngester
from ingest.finnhub import FinnhubIngester
from ingest.newsapi import NewsApiIngester
from ingest.alphavantage import AlphaVantageIngester

from .base import BaseAgent


class CollectAgent(BaseAgent):
    name = "collect"

    def run(self, input_data: dict) -> dict:
        ticker: str = input_data["ticker"]
        cfg: dict = input_data["cfg"]
        paths: dict = input_data["paths"]

        raw_dir: Path = paths["artifacts_dir"] / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        # ── 1단계: yfinance 단독 실행 ────────────────────────────────────────
        # company_name이 NewsAPI 검색어로 필요하고, 실패 시 파이프라인 즉시 중단
        self.log(f"[1/6] yfinance 수집 → {ticker}")
        yf_data = YahooIngester().fetch(ticker, cfg)
        write_json(raw_dir / "yfinance.json", yf_data)
        self.log(f"  → raw/yfinance.json 저장 (종가≈{yf_data['price']['current']})")

        # ── 종목 유형 검증 — ETF·레버리지·펀드는 재무 데이터 없어 분석 불가
        _UNSUPPORTED = {"ETF", "MUTUALFUND", "CRYPTOCURRENCY", "FUTURE", "INDEX"}
        quote_type = yf_data.get("info", {}).get("quoteType", "").upper()
        if quote_type in _UNSUPPORTED:
            type_label = {
                "ETF": "ETF/레버리지 상품",
                "MUTUALFUND": "뮤추얼 펀드",
                "CRYPTOCURRENCY": "암호화폐",
                "FUTURE": "선물",
                "INDEX": "지수",
            }.get(quote_type, quote_type)
            raise ValueError(
                f"[지원불가 종목] {ticker}는 {type_label}입니다. "
                "재무제표·EPS 등 필수 데이터가 없어 분석할 수 없습니다. "
                "개별 주식 티커를 입력해 주세요. (예: AAPL, TSLA, NVDA)"
            )

        company_name: str = yf_data.get("info", {}).get("longName", ticker)

        # ── 2단계: 나머지 5개 병렬 수집 ─────────────────────────────────────
        def _edgar() -> tuple[str, dict]:
            self.log(f"[2/6] SEC EDGAR 10-K 리스크 팩터 수집 → {ticker}")
            data = EdgarIngester().fetch(ticker)
            write_json(raw_dir / "edgar.json", data)
            if data.get("error"):
                self.log(f"  → raw/edgar.json 저장 (오류: {data['error']})")
            else:
                self.log(
                    f"  → raw/edgar.json 저장 | 10-K ({data['latest_10k_date']}) | "
                    f"리스크 {len(data['risk_factors'])}개"
                )
            return "edgar", data

        def _fred() -> tuple[str, dict]:
            self.log("[3/6] FRED 거시경제 지표 수집")
            data = FredIngester().fetch()
            write_json(raw_dir / "fred.json", data)
            if data.get("error"):
                self.log(f"  → raw/fred.json 저장 (오류: {data['error']})")
            else:
                self.log(f"  → raw/fred.json 저장 | {data['macro_summary'][:80]}...")
            return "fred", data

        def _finnhub() -> tuple[str, dict]:
            self.log(f"[4/6] Finnhub EPS·내부자 거래 수집 → {ticker}")
            data = FinnhubIngester().fetch(ticker)
            write_json(raw_dir / "finnhub.json", data)
            if data.get("error"):
                self.log(f"  → raw/finnhub.json 저장 (일부 오류: {data['error']})")
            else:
                eps = data.get("eps_surprises", [])
                insider = data.get("insider_transactions", [])
                self.log(f"  → raw/finnhub.json 저장 | EPS {len(eps)}분기 | 내부자 거래 {len(insider)}건")
            return "finnhub", data

        def _newsapi() -> tuple[str, dict]:
            self.log(f"[5/6] NewsAPI 뉴스 수집 → {company_name}")
            data = NewsApiIngester().fetch(ticker, company_name)
            write_json(raw_dir / "newsapi.json", data)
            if data.get("error"):
                self.log(f"  → raw/newsapi.json 저장 (오류: {data['error']})")
            else:
                s = data.get("sentiment", {})
                self.log(
                    f"  → raw/newsapi.json 저장 | 기사 {len(data['articles'])}개 | "
                    f"감성 점수: {s.get('score', 0)}"
                )
            return "newsapi", data

        def _alphavantage() -> tuple[str, dict]:
            self.log(f"[6/6] Alpha Vantage EPS·재무 심화 수집 → {ticker}")
            data = AlphaVantageIngester().fetch(ticker)
            write_json(raw_dir / "alphavantage.json", data)
            if data.get("error"):
                self.log(f"  → raw/alphavantage.json 저장 (오류: {data['error']})")
            else:
                eps_list = data.get("earnings", {}).get("quarterly_eps", [])
                self.log(
                    f"  → raw/alphavantage.json 저장 | EPS {len(eps_list)}분기 | "
                    f"연간 손익 {len(data.get('annual_income', []))}년"
                )
            return "alphavantage", data

        results: dict[str, Any] = {}
        tasks = [_edgar, _fred, _finnhub, _newsapi, _alphavantage]

        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = {executor.submit(fn): fn.__name__ for fn in tasks}
            for future in as_completed(futures):
                try:
                    key, data = future.result()
                    results[key] = data
                except Exception as e:
                    fn_name = futures[future]
                    self.log(f"  [병렬 수집 오류] {fn_name}: {e}")
                    results[fn_name.lstrip("_")] = {"error": str(e)}

        # ── 3단계: snapshot.json 합본 저장 ───────────────────────────────────
        snapshot = {
            **yf_data,
            "edgar":        results.get("edgar", {}),
            "fred":         results.get("fred", {}),
            "finnhub":      results.get("finnhub", {}),
            "newsapi":      results.get("newsapi", {}),
            "alphavantage": results.get("alphavantage", {}),
        }
        write_json(paths["artifacts_dir"] / "snapshot.json", snapshot)
        self.log("snapshot.json 저장 완료 (6개 소스 합본) → 다음 Agent 진행")

        return {**input_data, "snapshot": snapshot}
