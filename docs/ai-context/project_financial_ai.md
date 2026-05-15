---
name: financial-ai project overview
description: financial-ai 프로젝트 전체 구조, 파이프라인, 에이전트 구성, 퀀트 점수 체계 요약
type: project
originSessionId: 83b611bd-404a-4984-abd0-3e817e19a091
---
**주식 AI 분석 파이프라인** — 6개 외부 소스에서 데이터를 수집하고 LLM으로 리포트를 생성한 뒤 매매 신호를 출력하는 엔드투엔드 파이프라인.

**단일 진입점**: `scripts/run_pipeline.py`  
**프론트엔드**: `app.py` (Streamlit 대시보드, 4페이지)  
**시작 스크립트**: `start.sh`

## 파이프라인 (Orchestrator → 5 Agents 순차 실행)

1. **CollectAgent** (`src/agents/collect_agent.py`) — 6개 소스 수집 후 `artifacts/<ticker>/<date>/raw/` 저장
2. **AnalyzeAgent** — 피처/컨텍스트 구성 → `context.json`
3. **ReportAgent** — LLM으로 Markdown 리포트 → `reports/<ticker>/<date>.md`
4. **EvalAgent** — 루브릭 평가 → `eval.json`
5. **SignalAgent** — 매매 신호(buy/hold/sell) → `signal.json`, `tracking/prediction_log.csv`

## 데이터 소스 (5개)

- yfinance (Yahoo Finance) — 가격, 재무제표, 애널리스트 정보
- SEC EDGAR — 10-K 리스크 팩터
- FRED — 거시경제 지표 (fed funds rate, CPI YoY, GDP 등)
- Finnhub — EPS 서프라이즈, 내부자 거래
- NewsAPI — 뉴스/감성 분석 (티커·회사명 관련 기사만 필터링)

## 수집 전략 (CollectAgent 2단계)

1단계: yfinance 단독 실행 → company_name 확보 + **quoteType으로 ETF/레버리지 즉시 차단**  
2단계: 나머지 4개 소스 ThreadPoolExecutor 병렬 실행

## LLM 설정 (config.yaml)

- provider: openai 호환 (기본 gpt-4o-mini, base_url 변경으로 게이트웨이 사용 가능)
- api_key_env: OPENAI_API_KEY (.env 파일로 관리)
- 환경변수 FINANCIAL_AI_MODEL로 모델 덮어쓰기 가능

## 평가 시스템 (루브릭 100점)

- **M0** (rules-only): 3항목 — source_transparency(10), risk_coverage(10), forecast_verifiability(5) + 페널티
- **M2** (rules + LLM Judge): 6항목 추가 — data_accuracy(20), financial_quality(15), valuation_soundness(20), logic_consistency(10), bias_check(5), readability(5)
- `--judge` / `--no-judge` 플래그로 제어

## 퀀트 점수 (100점 만점) — 현재 구조

| 팩터 | 만점 | 학술 기반 |
|------|------|-----------|
| 밸류에이션 | 20 | Greenblatt Magic Formula |
| 퀄리티 | 25 | Novy-Marx(2013) + Fama-French RMW |
| 모멘텀 | 20 | Jegadeesh & Titman(1993) |
| 재무건전성 | 20 | Piotroski F-Score |
| 성장성 | 15 | 매출 YoY + EPS 성장 |

≥60점 → 매수 / 40~59점 → 중립 / <40점 → 매도  
세부 로직: `quant_scoring_design.md` 참조

## Confidence 계산 (signal.py + builder.py 협력)

- 퀀트 기준: 매수(60~100점) → 0.50~0.95 / 매도(0~40점) → 0.50~0.95 / 중립(40~60점) → 50점=0.65, 경계=0.50
- LLM 일치 → ×1.15(최대 0.95) / 한쪽 중립 → ×0.70(최소 0.35) / 완전 반대 → 0.35 고정

## 산출물 구조

```
artifacts/<ticker>/<date>/
  raw/yfinance.json, edgar.json, fred.json, finnhub.json, newsapi.json, alphavantage.json
  snapshot.json  (6개 소스 합본)
  context.json
  eval.json
  signal.json
reports/<ticker>/<date>.md
tracking/prediction_log.csv
```

## 배포 (AWS EC2)

- EC2 t3.small, Amazon Linux 2023, 싱가폴 리전, IP: 3.35.4.255
- GitHub Actions (`.github/workflows/deploy.yml`) — master push 시 자동 배포
- appleboy/ssh-action@v1.0.3, systemd 서비스(`financial-ai`)로 Streamlit 상시 실행
- .env는 GitHub Secrets 5개에서 생성 (OPENAI_API_KEY, NEWS_API_KEY, FRED_API_KEY, FINNHUB_API_KEY, ALPHA_VANTAGE_API_KEY)
- **핵심**: deploy.yml에 `env:` 블록으로 secrets → env var 매핑 필수 (없으면 .env 빈 파일 생성됨)
- 접속: http://3.35.4.255:8501
