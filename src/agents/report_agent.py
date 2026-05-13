"""ReportAgent — Jinja2 템플릿 + LLM으로 리포트 생성."""

from __future__ import annotations

from pathlib import Path

from report.composer import ContextBuilder, compose_markdown_report
from report.llm import LLMProvider

from .base import BaseAgent


_STUB_TEMPLATE = """# {ticker} ({name}) 투자 분석 리포트

### 1. 투자 요약
- **투자 의견**: 중립
- 목표가 $200 (PER 기반 단순 추정)
- 투자 기간: 12개월

### 2. 재무 현황
- trailing PER 약 {per} [출처: yf.info.trailingPE, {data_as_of}]

### 3. 성장 동력
- 서비스 매출 성장
- 단위당 마진 개선

### 4. 리스크 요인
- 경쟁 심화
- 규제 강화
- 금리 상승 시 멀티플 하락

### 5. 밸류에이션
- PER 배수 적용: 목표가 = EPS × PER 26 [출처: 단순 배수 가정]

### 6. 투자 결론
- 12개월 관점 중립. 멀티플 수축 시 하방 위험.
"""


class ReportAgent(BaseAgent):
    name = "report"

    def run(self, input_data: dict) -> dict:
        context: dict = input_data["context"]
        cfg: dict = input_data["cfg"]
        paths: dict = input_data["paths"]
        skip_llm: bool = input_data.get("skip_llm", False)
        prompts_dir: Path = input_data["prompts_dir"]
        ticker: str = input_data["ticker"]

        if skip_llm:
            self.log("--skip-llm → 스텁 리포트 사용")
            meta = context.get("metadata", {})
            report_md = _STUB_TEMPLATE.format(
                ticker=ticker,
                name=meta.get("company_name", ticker),
                per=context.get("valuation", {}).get("PER", "N/A"),
                data_as_of=meta.get("data_as_of", ""),
            )
            llm_provider = None
        else:
            self.log("LLM 리포트 생성 (Jinja2 + API)")
            llm_provider = LLMProvider(cfg)
            report_md = compose_markdown_report(llm_provider, prompts_dir, context)

        report_path = paths["report_md"]
        report_path.write_text(report_md, encoding="utf-8")
        self.log(f"{report_path.name} 저장 ({len(report_md)} chars)")

        return {**input_data, "report_md": report_md, "llm_provider": llm_provider}
