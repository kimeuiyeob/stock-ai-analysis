# Financial AI — 주식 분석 자동화 파이프라인

6개 외부 데이터 소스에서 실시간으로 정보를 수집하고, 퀀트 점수와 LLM 분석을 결합하여 투자 리포트와 매매 신호를 자동 생성하는 멀티 에이전트 파이프라인입니다.

> **AI 에이전트로 작업 시** `AGENTS.md`와 `docs/ai-context/`를 먼저 읽으세요. 설계 결정과 핵심 로직이 정리되어 있습니다.

> **⚠️ AI 에이전트 동기화 규칙**: 아래 파일을 수정할 때는 반드시 대응하는 `docs/ai-context/` 문서도 함께 갱신하세요.
> | 수정 파일 | 함께 갱신할 문서 |
> |-----------|-----------------|
> | `src/features/builder.py` (퀀트 점수) | `docs/ai-context/quant_scoring_design.md` |
> | `src/trading_stub/signal.py` (신뢰도·신호) | `docs/ai-context/signal_target_stoploss.md` |
> | `app.py` (도움말 텍스트) | `docs/ai-context/ui_helptext_spec.md` |
> | 파이프라인 구조·에이전트·배포 | `docs/ai-context/project_financial_ai.md` |
> 
> 이 규칙을 지키지 않으면 다음 AI 세션이 잘못된 컨텍스트로 작업하게 됩니다.

> **투자 권유가 아닙니다.** 생성 결과물은 참고용이며, 실제 투자 판단 전 반드시 원자료와 교차 검증하세요.

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [아키텍처 — 멀티 에이전트 파이프라인](#2-아키텍처--멀티-에이전트-파이프라인)
3. [데이터 소스 (6개)](#3-데이터-소스-6개)
4. [퀀트 점수 산정 방식](#4-퀀트-점수-산정-방식-100점)
5. [리포트 생성](#5-리포트-생성)
6. [평가 시스템 — 루브릭 100점](#6-평가-시스템--루브릭-100점)
7. [매매 신호 산정 방식](#7-매매-신호-산정-방식)
8. [Streamlit 대시보드](#8-streamlit-대시보드)
9. [백테스트](#9-백테스트)
10. [설치 및 실행](#10-설치-및-실행)
11. [설정 (config.yaml)](#11-설정-configyaml)
12. [CLI 옵션 전체](#12-cli-옵션-전체)
13. [환경변수](#13-환경변수)
14. [디렉터리 구조](#14-디렉터리-구조)
15. [문제 해결](#15-문제-해결)

---

## 1. 시스템 개요

| 항목 | 내용 |
|------|------|
| 단일 진입점 | `scripts/run_pipeline.py` |
| 웹 대시보드 | `app.py` → `streamlit run app.py` (포트 8501) |
| 빠른 시작 | `bash start.sh` |
| LLM 백엔드 | OpenAI 호환 API (OpenAI 직접 또는 커스텀 게이트웨이), Anthropic 지원 |
| 데이터 소스 | Yahoo Finance · SEC EDGAR · FRED · Finnhub · NewsAPI · Alpha Vantage |
| 산출물 | Markdown 리포트, eval.json, signal.json, tracking CSV |

---

## 2. 아키텍처 — 멀티 에이전트 파이프라인

`Orchestrator`가 5개 Agent를 순서대로 구동하며, 각 Agent는 공유 상태(`state dict`)를 받아 처리 후 다음 Agent로 전달합니다.

```
scripts/run_pipeline.py
        │
        ▼
  Orchestrator
        │
   ┌────┴────────────────────────────────────────────┐
   │  1. CollectAgent   → raw/*.json + snapshot.json │
   │  2. AnalyzeAgent   → context.json               │
   │  3. ReportAgent    → reports/<ticker>/<date>.md  │
   │  4. EvalAgent      → eval.json                  │
   │  5. SignalAgent    → signal.json + prediction_log.csv │
   └─────────────────────────────────────────────────┘
```

### Agent별 역할

| Agent | 파일 | 주요 동작 |
|-------|------|-----------|
| **CollectAgent** | `src/agents/collect_agent.py` | 6개 소스 수집 → `raw/` 개별 저장 → `snapshot.json` 합본 생성 |
| **AnalyzeAgent** | `src/agents/analyze_agent.py` | `FeatureBuilder`로 퀀트 지표 계산 → `ContextBuilder`로 LLM 입력 컨텍스트 구성 → `context.json` 저장, 토큰 예산(5,000 토큰) 확인 |
| **ReportAgent** | `src/agents/report_agent.py` | Jinja2(`report.j2`) 템플릿 렌더링 → LLM에 시스템/유저 메시지 분리 전송 → Markdown 리포트 저장 |
| **EvalAgent** | `src/agents/eval_agent.py` | 규칙 엔진(M0) 자동 채점 → 옵션: LLM Judge(M2) 6항목 추가 채점 → `eval.json` 저장 |
| **SignalAgent** | `src/agents/signal_agent.py` | 리포트에서 투자 신호 추출 → 퀀트 신호와 결합 → `signal.json` 저장, `prediction_log.csv` 기록 |

---

## 3. 데이터 소스 (6개)

모든 소스 데이터는 `artifacts/<ticker>/<date>/raw/` 에 개별 저장되며, `snapshot.json`에 합본됩니다.

### 1) Yahoo Finance (`yfinance`) — `raw/yfinance.json`
- **수집 데이터**: 일별·월별 종가(기본 1년), 52주 고/저, 회사 기본 정보(섹터·시가총액·PER·PBR·ROE 등 16개 필드), 분기/연간 손익계산서·대차대조표·현금흐름표, 최신 뉴스(최대 10건), 애널리스트 추천(strongBuy/buy/hold/sell/strongSell 집계 + 컨센서스 목표주가)
- **재시도**: 최대 3회, 지수 백오프(2^n초)
- **한국 종목**: `005930.KS` 형식 지원 (재무 필드가 비는 경우 있음)

### 2) SEC EDGAR — `raw/edgar.json`
- **수집 데이터**: 최신 10-K 제출일, 리스크 팩터(Risk Factors) 섹션 텍스트
- **활용**: LLM 리포트의 리스크 분석 섹션에서 공식 리스크 팩터 직접 인용
- **비미국 종목**: 조회 실패 시 `error` 필드에 기록 후 파이프라인 계속 진행

### 3) FRED (Federal Reserve Economic Data) — `raw/fred.json`
- **수집 데이터**: 기준금리(Fed Funds Rate), 10년물 국채금리, CPI(YoY), 실업률, GDP 성장률, 달러 인덱스
- **API 키**: `FRED_API_KEY` 환경변수 필요. 키 없을 시 매크로 섹션 비활성화

### 4) Finnhub — `raw/finnhub.json`
- **수집 데이터**: EPS 서프라이즈(실제 vs 컨센서스 추정치, 분기별), 내부자 거래 내역(최대 5건), 뉴스 감성, 애널리스트 추천
- **API 키**: `FINNHUB_API_KEY` 환경변수 필요

### 5) NewsAPI — `raw/newsapi.json`
- **수집 데이터**: 회사명 기반 최신 뉴스 기사(최대 5건), 감성 분석(긍정/부정/중립 키워드 기반 점수화)
- **API 키**: `NEWSAPI_KEY` 환경변수 필요

### 6) Alpha Vantage — `raw/alphavantage.json`
- **수집 데이터**: 회사 개요, 분기별 EPS 추이(어닝 서프라이즈 포함), 연간 손익계산서
- **API 키**: `ALPHAVANTAGE_API_KEY` 환경변수 필요

---

## 4. 퀀트 점수 산정 방식 (100점)

`src/features/builder.py`의 `FeatureBuilder._compute_quant_score()`가 계산하며, 5개 팩터를 합산합니다.  
세부 채점 기준 전문은 `docs/ai-context/quant_scoring_design.md`를 참조하세요.

| 팩터 | 만점 | 학술 기반 |
|------|------|-----------|
| 밸류에이션 | 20점 | Greenblatt Magic Formula |
| 퀄리티 | 25점 | Novy-Marx(2013) + Fama-French RMW |
| 모멘텀 | 20점 | Jegadeesh & Titman(1993) |
| 재무건전성 | 20점 | Piotroski F-Score |
| 성장성 | 15점 | 매출 YoY + EPS 성장 |

### 밸류에이션 (20점) — Greenblatt Magic Formula

- **EV/EBITDA (10점)**: 일반 ≤8배→10점 / ≤12배→8점 / ≤16배→6점 / ≤22배→3점  
  유틸리티·에너지 섹터는 인프라 특성상 기준 완화 (≤12배→10점 / ≤18배→8점 / ≤25배→6점 / ≤35배→3점)
- **FCF 수익률 (6점)**: >7%→6점 / >4%→5점 / >2%→3점 / >0%→1점
- **Forward PER (4점)**: ≤12배→4점 / ≤17배→3점 / ≤22배→2점 / ≤30배→1점

### 퀄리티 (25점) — Novy-Marx + Fama-French RMW

- **총이익률 (5점)**: >55%→5점 / >35%→3점 / >15%→1점
- **영업이익률 (8점)**: >25%→8점 / >15%→6점 / >8%→4점 / >0%→2점
- **ROE (7점)**: >25%→7점 / >15%→5점 / >8%→3점 / >0%→1점
- **ROA (5점)**: >15%→5점 / >8%→4점 / >4%→2점 / >0%→1점

### 모멘텀 (20점) — Jegadeesh & Titman(1993)

최근 1개월 제외(단기 역전 회피), 월별 종가 기준.

- **12-1개월 수익률 (12점)**: >30%→12점 / >15%→9점 / >5%→6점 / >0%→3점
- **6-1개월 수익률 (8점)**: >15%→8점 / >5%→6점 / >0%→3점

### 재무건전성 (20점) — Piotroski F-Score

- **순부채/FCF 상환연수 (8점)**: 순현금→8점 / ≤2년→6점 / ≤4년→4점 / ≤7년→2점
- **Debt/Equity (7점)**: ≤20%→7점 / ≤50%→5점 / ≤100%→3점 / ≤200%→1점
- **FCF 품질 (5점)**: FCF>0→+3점, FCF≥순이익×80%→+2점

> **섹터 보정**: 금융·부동산은 레버리지가 사업 구조상 필수이므로 D/E·순부채 항목을 중립값으로 처리합니다.

### 성장성 (15점)

- **매출 YoY (9점)**: >25%→9점 / >15%→7점 / >7%→4점 / >0%→1점
- **EPS 성장률 (6점)**: >25%→6점 / >10%→4점 / >0%→2점

> **데이터 누락 처리**: 데이터가 없는 항목은 0점이 아닌 만점의 절반(중립값)으로 처리됩니다. 불완전한 데이터가 강한 매도 신호를 만드는 것을 방지하기 위함입니다.

### 퀀트 방향 결정 및 신뢰도

| 총점 | 방향 | 신뢰도 공식 |
|------|------|------------|
| 60점 이상 | **매수 (buy)** | 0.50 + (점수 − 60) / 40 × 0.45 → 0.50~0.95 |
| 40점 미만 | **매도 (sell)** | 0.50 + (40 − 점수) / 40 × 0.45 → 0.50~0.95 |
| 40~59점 | **중립 (hold)** | 0.65 − \|점수 − 50\| / 10 × 0.15 → 0.50~0.65 |

> 데이터 완성도가 낮을수록 신뢰도는 중립(50점)으로 수렴합니다: `조정 점수 = 원점수 × 완성도 + 50 × (1 − 완성도)`

---

## 5. 리포트 생성

`src/report/composer.py` + `prompts/report.j2` Jinja2 템플릿으로 구성됩니다.

### 리포트 구성 (6개 섹션)

| 섹션 | 주요 내용 |
|------|---------|
| **1. 투자 요약** | 투자 의견(매수/중립/매도), 목표가, 투자 기간, 핵심 논거 3~5줄, 주요 리스크 |
| **2. 재무 현황** | 최근 4분기 매출·영업이익·순이익 추이, YoY 성장률, FCF·부채·유동성 분석, 밸류에이션 배수(PER·PBR·EV/EBITDA), EPS 서프라이즈(Finnhub), Alpha Vantage 연간 손익 |
| **3. 성장 동력** | 핵심 드라이버 3~4개, TAM·성장률·경쟁 우위, 뉴스 감성 반영 |
| **4. 리스크 요인** | 리스크 4~5개(발생 가능성·주가 영향 추정), SEC 10-K 공식 리스크 직접 인용, FRED 거시 지표 |
| **5. 밸류에이션** | 방법론 선택 근거, 목표가 산식 단계별 명시, 상방·기본·하방 시나리오 3종, 현재가 대비 상승여력 |
| **6. 투자 결론** | 최종 의견 재확인, 목표가·손절가·기간 명시, 분석이 틀릴 수 있는 조건 2가지, 모니터링 지표 |

### 목표가 산정 방식

LLM이 섹션 5(밸류에이션)에서 직접 산출하며, 아래 방법론 중 해당 종목에 적합한 것을 선택합니다.

- **PER 기반**: `Forward EPS × 목표 PER = 목표가`
- **DCF (현금흐름 할인)**: 예상 FCF + 할인율 + 성장률 가정
- **PBR 기반**: `BPS × 목표 PBR = 목표가`
- **EV/EBITDA 기반**: `EBITDA × 목표 배수 − 순부채 = 시가총액`

낙관·기본·비관 3가지 시나리오를 각각 산출하며, `signal.json`의 `target_price`는 **기본 시나리오** 값입니다.

### LLM 인터페이스

리포트 마지막에 아래 JSON을 출력하도록 프롬프트가 요구하며, `SignalAgent`가 정규식으로 파싱합니다.

```json
{
  "signal": "buy",
  "target_price": 215.0,
  "stop_loss": 162.0,
  "horizon": "12m",
  "upside_pct": 12.5,
  "key_risk": "관세 리스크로 인한 공급망 비용 증가"
}
```

### --skip-llm 모드

API 없이 파이프라인 구조만 검증할 때 사용합니다. 스텁 리포트(현재가·PER 기반 단순 템플릿)를 생성하며, 평가·신호 단계는 정상 실행됩니다.

---

## 6. 평가 시스템 — 루브릭 100점

리포트 품질을 자동으로 수치화하는 이중 채점 구조입니다. **규칙 엔진(M0)** 이 측정 가능한 형식 항목을 자동 채점하고, **LLM Judge(M2)** 가 정성적 판단이 필요한 항목을 추가 채점합니다. 두 방식은 독립적으로 설계되어 중복 채점하지 않습니다.

관련 파일: `src/eval/rules.py` · `src/eval/judge.py` · `src/eval/rubric.py`

---

### 루브릭 9개 항목 전체

| # | 항목 | 만점 | 채점 주체 | 측정 대상 |
|---|------|------|----------|----------|
| 1 | `source_transparency` | 10점 | 규칙 자동 | 숫자에 출처 태그 부착 비율 |
| 2 | `risk_coverage` | 10점 | 규칙 자동 | 필수 리스크 키워드 커버 비율 |
| 3 | `forecast_verifiability` | 5점 | 규칙 자동 | 목표가 + 산식 존재 여부 |
| 4 | `data_accuracy` | 20점 | LLM Judge | 원자료 숫자·단위 일치, 환각 감지 |
| 5 | `financial_quality` | 15점 | LLM Judge | 현금흐름·부채·이익·성장 논의 깊이 |
| 6 | `valuation_soundness` | 20점 | LLM Judge | 밸류에이션 방법론·가정·하방 시나리오 |
| 7 | `logic_consistency` | 10점 | LLM Judge | 투자 의견과 데이터·근거 연결의 일관성 |
| 8 | `bias_check` | 5점 | LLM Judge | 확증편향·일방적 낙관·악재 누락 여부 |
| 9 | `readability` | 5점 | LLM Judge | 결론·근거·리스크·추적 지표 명확성 |

**이론상 만점**: 100점 (양수 항목 합계) + 페널티 감점

---

### M0 모드 — 규칙 자동 채점 (3/9 항목)

API 없이 또는 `--no-judge` 상태에서 동작합니다. 형식적으로 측정 가능한 3개 항목만 자동 채점하며, **25점 만점을 100점으로 환산**하여 등급을 결정합니다.

#### 항목 1. `source_transparency` (출처 투명성) — 10점 만점

숫자 데이터에 `[출처: 필드명, 기준일]` 형식의 태그가 얼마나 붙어 있는지를 비율로 계산합니다.

```
출처 부착 비율 = 출처 태그가 있는 숫자 수 ÷ 전체 숫자 수
점수 = min(10, round(출처 부착 비율 × 10))
```

| 출처 부착 비율 | 점수 예시 |
|-------------|---------|
| 100% | 10점 |
| 70% | 7점 |
| 50% (경고 플래그) | 5점 |
| 30% | 3점 |

> 50% 미만이면 경고 플래그가 `eval.json`의 `flags` 배열에 기록됩니다.  
> 예: `"출처 누락: 숫자 42개 중 18개만 출처 표기 (43%)"`

#### 항목 2. `risk_coverage` (리스크 커버리지) — 10점 만점

리포트 본문에 아래 9개 필수 리스크 키워드가 몇 개나 등장하는지 측정합니다.

**필수 리스크 키워드 9개**: `경쟁`, `규제`, `금리`, `환율`, `멀티플`, `실적`, `부진`, `하락`, `감소`

```
커버 개수 = 리포트 본문에 등장한 키워드 수 (최대 9개)
점수 = round(커버 개수 ÷ 9 × 10)
```

| 커버된 키워드 수 | 점수 | 상태 |
|--------------|------|------|
| 9개 | 10점 | 정상 |
| 7개 | 8점 | 정상 |
| 5개 | 6점 (경고 플래그) | 경고 |
| 3개 이하 | 3점 이하 (경고 플래그) | 부족 |

> 점수 6점 미만이면 미커버 키워드 목록이 플래그로 기록됩니다.  
> 예: `"리스크 미흡: 미커버 키워드 → ['환율', '멀티플', '부진']"`

#### 항목 3. `forecast_verifiability` (예측 검증 가능성) — 5점 만점

투자 의견의 재현 가능성을 검증합니다. 목표가가 있어도 산출 근거(산식)가 없으면 다른 분석가가 검증할 수 없다는 원칙에서 설계되었습니다.

| 조건 | 점수 |
|------|------|
| 목표가 기재 **AND** 산식 명시 (EPS × PER, DCF 등) | **5점** |
| 목표가는 있으나 산식 미제시 | **3점** + 플래그 기록 |
| 목표가 자체 미기재 | **0점** + 플래그 기록 |

> 탐지 패턴:
> - 목표가: `목표가` + `$` 또는 숫자 조합
> - 산식: `PER`, `DCF`, `EPS`, `PBR`, `배수`, `할인율`, `×`, `=` 등 포함 여부

---

### 페널티 항목 (자동 감점)

M0·M2 모두 적용됩니다. 양수 점수 합계에서 차감합니다.

#### 페널티 1. 과도한 확신 표현 — **−3점**

아래 패턴 중 하나라도 감지되면 즉시 감점합니다. 투자 리포트에서 절대적 확신 표현은 독자를 오도할 수 있어 엄격하게 적용합니다.

탐지 패턴:
- `무조건 오른다`, `무조건 상승`, `무조건 매수`
- `확실히 오른다`, `확실히 상승`
- `반드시 이익`, `반드시 오른다`
- `놓치면 안 된다`, `손실 없다`
- `100% 확실`, `100% 보장`, `100% 수익`

> 예: `"과도한 확신 표현 2건 감지 → −3점"`

#### 페널티 2. PER 데이터 불일치 — **−5점**

리포트 본문에 기재된 Trailing PER과 원자료(yfinance)의 실제 PER을 비교하여, 차이가 **2.0 초과**이면 감점합니다. LLM이 수치를 잘못 인용하거나 환각했을 가능성을 자동으로 탐지하는 장치입니다.

```
|리포트 PER − 원자료 PER| > 2.0  →  −5점
```

> 탐지 방법: 리포트 섹션 2(재무 현황)에서 Trailing/실적 PER을 정규식으로 추출하되, 밸류에이션 섹션의 "목표가 = EPS × PER 26" 같은 적용 배수는 제외합니다.  
> 예: `"PER 불일치: 리포트=28.4, 원자료=35.2 → −5점"`

---

### M0 점수 → 등급 환산 공식

M0에서는 3개 항목만 채점되므로 25점이 사실상의 만점입니다. 이를 100점 척도로 환산하여 M2와 동일한 등급 기준을 적용합니다.

```
total_score       = source_transparency + risk_coverage + forecast_verifiability + 페널티
raw_for_scale     = max(0, total_score)   # 음수 방지
score_normalized_100 = min(100, (raw_for_scale ÷ 25) × 100)
등급              = score_normalized_100 기준으로 판정
```

**M0 점수 예시**

| 상황 | 원점수 | 환산 점수 | 등급 |
|------|-------|---------|------|
| 출처 8점 + 리스크 8점 + 목표가+산식 5점 | 21점 | 84점 | B |
| 출처 10점 + 리스크 10점 + 목표가+산식 5점 (페널티 없음) | 25점 | 100점 | A |
| 출처 5점 + 리스크 4점 + 목표가만 3점 | 12점 | 48점 | C |
| 출처 3점 + 리스크 3점 + 목표가 없음 + PER 불일치 | 1점 | 4점 | D |

---

### M2 모드 — LLM Judge 추가 채점 (6/9 항목)

`--judge` 플래그 또는 `config.yaml`의 `eval.use_llm_judge: true`로 활성화됩니다.

LLM Judge는 **독립 심사위원** 역할로 동작하며, 규칙 엔진이 이미 채점한 3개 항목(출처·리스크·목표가)은 제외하고 정성적 판단이 필요한 6개 항목만 채점합니다. 원자료(`context.json`)와 리포트 본문만을 근거로 사용하며, 외부 지식으로 사실을 보강하지 않도록 프롬프트에서 명시적으로 제한합니다.

**Judge 설정값**
- `judge_temperature: 0.15` — 리포트 생성(0.2)보다 낮게 설정하여 채점 일관성을 높임
- `judge_max_tokens: 2500`

#### Judge 6개 항목 상세

**① `data_accuracy` (데이터 정확성) — 20점**  
원자료 JSON과 리포트 본문의 숫자·단위를 대조하여 환각(hallucination) 또는 오인용을 탐지합니다. 매출, EPS, 시가총액, 성장률 등 핵심 수치가 실제 데이터와 다를수록 감점됩니다. 루브릭에서 가장 높은 배점(20점)인 이유는 수치 오류가 투자 판단에 직결되기 때문입니다.

**② `financial_quality` (재무 분석 깊이) — 15점**  
현금흐름(FCF), 부채 구조, 이익 마진 추이, 성장률 등 재무 논의의 깊이와 정확성을 평가합니다. 단순 수치 나열에 그치지 않고, 수치 간 관계(예: 매출 성장에도 마진 하락 원인 분석)를 설명하는지 여부가 핵심입니다.

**③ `valuation_soundness` (밸류에이션 타당성) — 20점**  
적용한 밸류에이션 방법론의 선택 근거, 가정(성장률·할인율·목표 PER)의 합리성, 상방·기본·하방 3가지 시나리오 존재 여부를 평가합니다. 데이터 정확성과 함께 20점으로 가장 높은 배점을 부여한 이유는 목표가의 신뢰성이 분석의 핵심 가치이기 때문입니다.

**④ `logic_consistency` (논리 일관성) — 10점**  
투자 의견(매수/중립/매도)과 본문에 제시된 근거·데이터가 모순 없이 연결되는지 평가합니다. 예를 들어 본문에서 실적 부진을 서술하면서 결론이 "강력 매수"인 경우 감점됩니다.

**⑤ `bias_check` (편향 점검) — 5점**  
확증편향, 일방적 낙관론, 중요 악재 누락 여부를 평가합니다. 긍정적 측면만 부각하고 리스크를 형식적으로만 언급한 경우 감점됩니다. 이 항목은 만점이 낮은(5점) 대신 리포트의 균형성을 담보하는 역할을 합니다.

**⑥ `readability` (가독성) — 5점**  
결론이 명확한지, 근거가 논리적 흐름으로 제시되는지, 리스크와 모니터링 지표가 구체적인지를 평가합니다. 형식적 구조(섹션 구성)보다 실질적 이해 가능성을 기준으로 합니다.

#### M2 최종 점수 계산

```
total_score = (규칙 3항목 합계) + (Judge 6항목 합계) + 페널티
score_normalized_100 = max(0, min(100, total_score))   # 그대로 100점 스케일
```

M2에서는 `total_score` 자체가 100점 스케일의 직접적인 의미를 가집니다.

**M2 점수 예시**

| 항목 | 점수 |
|------|------|
| `source_transparency` (규칙) | 8 / 10 |
| `risk_coverage` (규칙) | 9 / 10 |
| `forecast_verifiability` (규칙) | 5 / 5 |
| `data_accuracy` (Judge) | 16 / 20 |
| `financial_quality` (Judge) | 12 / 15 |
| `valuation_soundness` (Judge) | 15 / 20 |
| `logic_consistency` (Judge) | 8 / 10 |
| `bias_check` (Judge) | 4 / 5 |
| `readability` (Judge) | 4 / 5 |
| **소계** | **81점** |
| PER 불일치 페널티 | −5점 |
| **total_score** | **76점** |
| **등급** | **B** |

---

### 등급 판정 기준

환산 점수(`score_normalized_100`) 기준으로 4단계 등급을 부여합니다. 이 기준은 M0·M2 모두 동일하게 적용됩니다.

| 환산 점수 | 등급 | 의미 |
|---------|------|------|
| **85점 이상** | **A** | 의사결정 보조 가능 — 데이터 출처·논리·밸류에이션이 충분히 검증되어 참고 자료로 활용 가능. 단, 원자료와의 최종 교차 검증은 항상 필요 |
| **70점 이상** | **B** | 참고 가능 — 전반적으로 구조는 갖추었으나, 핵심 수치나 밸류에이션 가정 중 재검증이 필요한 부분이 존재 |
| **50점 이상** | **C** | 투자 판단 부족 — 논리 흐름 또는 데이터 근거가 불충분하여 단독 사용 불가. 보강 후 재실행 권장 |
| **50점 미만** | **D** | 폐기 또는 전면 재작성 권장 — 수치 오류·논리 모순·편향 등 구조적 문제가 있어 판단 자료로 부적합 |

---

### `eval.json` 출력 구조

평가 결과는 `artifacts/<ticker>/<date>/eval.json`에 저장됩니다.

```json
{
  "ticker": "AAPL",
  "report_date": "2026-05-11",
  "total_score": 76.0,
  "score_normalized_100": 76.0,
  "rubric_mode": "M2_full",
  "auto_coverage": "9/9 항목 자동 채점 (M2_full)",
  "grade": "B: 참고 가능, 핵심 숫자·가정 재검증 필요",
  "grade_note": "M2: 규칙(출처·리스크·목표가·페널티) + LLM Judge 6항목 합산. 이론 만점 100 + 페널티. total_score=76.0.",
  "penalty": -5,
  "breakdown": {
    "source_transparency": 8,
    "risk_coverage": 9,
    "forecast_verifiability": 5,
    "data_accuracy": 16,
    "financial_quality": 12,
    "valuation_soundness": 15,
    "logic_consistency": 8,
    "bias_check": 4,
    "readability": 4
  },
  "flags": [
    "출처 누락: 숫자 40개 중 32개만 출처 표기 (80%)",
    "PER 불일치: 리포트=28.4, 원자료=35.2 → −5점",
    "Judge: 밸류에이션 하방 시나리오 가정이 다소 낙관적이며 금리 리스크 반영이 부족함"
  ]
}
```

> **M0 모드일 때 차이점**: `rubric_mode`가 `"M0_rules_only"`, `breakdown`의 Judge 6항목은 `null`, `score_normalized_100`은 원점수를 25점 기준 환산한 값으로 기록됩니다.

---

## 7. 매매 신호 산정 방식

`src/trading_stub/signal.py`가 퀀트 신호와 LLM 신호를 결합하여 최종 방향과 신뢰도를 결정합니다.

### Step 1: LLM 신호 추출

리포트 마지막 JSON 블록에서 `signal` 필드를 파싱합니다. JSON 파싱 실패 시 본문 정규식 fallback (`투자 의견 … 매수/중립/매도`)을 사용합니다.

### Step 2: 퀀트 신호와 결합 (신뢰도 조정)

| 상황 | 최종 방향 | 신뢰도 |
|------|----------|--------|
| LLM = 퀀트 (일치) | LLM 방향 | 퀀트 신뢰도 × 1.15 (최대 0.95) |
| 한쪽이 hold | 방향성 있는 쪽 채택 | 퀀트 신뢰도 × 0.70 (최소 0.35) |
| buy vs sell (완전 반대) | hold | 0.35 (고정) |
| LLM 신호 없음 | 퀀트 방향 | 퀀트 신뢰도 그대로 |

### Step 3: 투자 기간

리포트 JSON의 `horizon` 필드를 우선 사용하며, 없을 경우 본문 정규식(`12개월/6개월/3개월/1개월`)으로 추출합니다. 기본값은 `12m`.

### 목표가 / 손절가

| 항목 | 산정 방식 |
|------|---------|
| **목표가** | LLM 리포트 JSON 블록에서 추출 → 3단계 검증(방향·범위·컨센서스 대조) 후 확정. 실패 시 월가 애널리스트 평균 컨센서스로 대체 |
| **손절가** | ATR(14일) × 2배를 현재가에서 차감. ATR 없을 경우 현재가 × 0.82(−18%). **매수 신호에만 적용** |
| **상승여력(△)** | (목표가 − 현재가) / 현재가 × 100 |

> **목표가 검증 3단계**: ① 매수 신호인데 목표가 < 현재가 → 폐기  ② 현재가 대비 ±60% 초과 이탈 → 폐기  ③ 월가 컨센서스와 60% 이상 괴리 → 두 값의 평균으로 자동 보정

### 예측 이력 기록 (`tracking/prediction_log.csv`)

매 실행마다 아래 컬럼을 CSV에 추가합니다. 미래 성과 검증용 컬럼은 실행 시 공백으로 남깁니다.

| 컬럼 | 설명 |
|------|------|
| `date` | 분석 날짜 |
| `ticker` | 종목 코드 |
| `price_at_report` | 분석 시점 현재가 |
| `opinion` | 매매 방향 (buy/hold/sell) |
| `target_price` | 목표가 |
| `stop_loss` | 손절가 |
| `horizon` | 투자 기간 |
| `confidence` | 신뢰도 (0~1) |
| `quant_score` | 퀀트 점수 (0~100) |
| `rubric_score` | 리포트 평가 원점수 |
| `3m_actual_price` | 3개월 후 실제가 (수동 입력) |
| `12m_actual_price` | 12개월 후 실제가 (수동 입력) |
| `direction_hit_3m` | 3개월 방향 적중 여부 |
| `direction_hit_12m` | 12개월 방향 적중 여부 |
| `target_hit` | 목표가 달성 여부 |
| `pe_actual` | 분석 시점 Trailing PER (yfinance) |

---

## 8. Streamlit 대시보드

```bash
bash start.sh
# 또는
streamlit run app.py --server.port 8501
```

### 페이지 구성

| 페이지 | 내용 |
|--------|------|
| **📊 대시보드** | 전체 종목 요약 카드(3열), 매수/중립/매도 집계, 퀀트 점수 프로그레스바, 현재가·목표가·손절가·상승여력, 퀀트 세부 점수 expandable |
| **📄 리포트** | 종목·날짜 선택 → Markdown 전문 표시, 사이드바에 신호·신뢰도·퀀트 점수·목표가·손절가·평가 점수·플래그 |
| **🚀 주식 분석** | 티커 입력 → 파이프라인 실행(별도 subprocess), 실시간 로그 스트리밍(1초 갱신), LLM Judge ON/OFF 선택 |
| **📈 예측 이력** | prediction_log.csv 기반 전체 기록 조회, 종목·신호 필터, 신호 분포·퀀트 점수 차트 |

---

## 9. 백테스트

`src/backtest/runner.py`의 `BacktestRunner`는 과거 시점의 퀀트 점수를 **실제 재무 데이터**로 재계산하고, 이후 3개월·6개월·12개월 수익률을 측정합니다.

- **reporting_lag**: 60일 적용 (분기 실적 발표 지연 반영)
- **모멘텀**: 실제 과거 일별 종가 기준 (1개월=21거래일, 3개월=63일, 12개월=252일)
- **애널리스트**: 과거 데이터 미가용 → 7점(중립) 고정
- **백테스트 기간**: 기본 3년, 분기별(`QE`) 시점

```python
from src.backtest.runner import BacktestRunner
df = BacktestRunner().run("AAPL", years=3)
print(df[["date", "score", "forward_return_3m", "forward_return_12m"]])
```

---

## 10. 설치 및 실행

### 환경 설정

```bash
cd financial-ai
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### API 키 설정

`financial-ai/.env` 파일을 생성하고 필요한 키를 입력합니다.

```bash
# financial-ai/.env
OPENAI_API_KEY=sk-...          # 필수 (LLM 리포트 생성)
FRED_API_KEY=...               # 선택 (거시경제 지표)
FINNHUB_API_KEY=...            # 선택 (EPS 서프라이즈, 내부자 거래)
NEWSAPI_KEY=...                # 선택 (뉴스 기사)
ALPHAVANTAGE_API_KEY=...       # 선택 (EPS 심화, 연간 손익)
```

선택 키가 없으면 해당 소스는 스킵되며, 리포트에 "데이터 미제공"으로 표기됩니다.

### 실행

```bash
# 단일 종목
python scripts/run_pipeline.py --ticker AAPL

# 특정 날짜로 실행
python scripts/run_pipeline.py --ticker TSLA --date 2026-05-11

# 배치 (쉼표 구분)
python scripts/run_pipeline.py --tickers AAPL,MSFT,NVDA

# 배치 (공백 구분)
python scripts/run_pipeline.py --ticker AAPL MSFT NVDA

# 파일 기반 배치 (한 줄 1티커, # 주석 가능)
python scripts/run_pipeline.py --tickers-file tickers.txt

# LLM Judge(M2) 활성화
python scripts/run_pipeline.py --ticker AAPL --judge

# LLM 생략 (파이프라인 구조 검증용)
python scripts/run_pipeline.py --ticker AAPL --skip-llm

# 대시보드
bash start.sh
```

### 배치 실행 결과

`artifacts/_batch/<날짜>/batch_summary.json`에 성공·실패 종목 요약이 저장됩니다. 실패 종목이 있으면 `errors.json`이 함께 생성됩니다. 종목 간 요청 간격은 `config.yaml`의 `ingest.sleep_between_tickers`(기본 1초)로 조정합니다.

---

## 11. 설정 (config.yaml)

```yaml
llm:
  provider: "openai"          # "openai" 또는 "anthropic"
  model: "gpt-4o-mini"        # 사용할 모델 ID
  temperature: 0.2            # 리포트 생성 온도 (낮을수록 일관성 높음)
  max_tokens: 4096
  base_url: "https://api.openai.com/v1"   # 커스텀 게이트웨이 URL로 교체 가능
  api_key_env: "OPENAI_API_KEY"           # 키를 읽을 환경변수 이름

ingest:
  price_period: "1y"          # yfinance 가격 수집 기간
  news_count: 10              # Yahoo 뉴스 최대 수집 건수
  retry_attempts: 3           # yfinance 실패 시 재시도 횟수
  sleep_between_tickers: 1    # 배치 실행 시 종목 간 대기 시간(초)

eval:
  use_llm_judge: true         # M2 LLM Judge 기본 활성화 여부
  judge_max_tokens: 2500
  judge_temperature: 0.15

paths:
  artifacts: "artifacts"
  reports: "reports"
  tracking: "tracking"
  prompts: "prompts"
```

---

## 12. CLI 옵션 전체

| 옵션 | 설명 |
|------|------|
| `--ticker AAPL [MSFT ...]` | 단일 또는 여러 티커 (공백 구분) |
| `--tickers AAPL,MSFT,NVDA` | 쉼표 구분 배치 |
| `--tickers-file PATH` | 파일 기반 배치 (한 줄 1티커, `#` 주석 가능) |
| `--date YYYY-MM-DD` | 산출물 저장 날짜 폴더 (기본: 오늘 UTC) |
| `--skip-llm` | LLM 호출 생략, 스텁 리포트 생성 |
| `--judge` | 이번 실행만 LLM Judge(M2) 강제 활성화 |
| `--no-judge` | 이번 실행만 LLM Judge 비활성화 |
| `--list-models` | 설정된 게이트웨이의 사용 가능 모델 목록 조회 후 종료 |
| `--model-log` | 모델 목록 조회 후 `logs/` 에 저장 (파이프라인은 정상 실행) |

---

## 13. 환경변수

| 변수 | 필수 여부 | 설명 |
|------|----------|------|
| `OPENAI_API_KEY` | 필수 | LLM 리포트·Judge 호출 API 키 |
| `FRED_API_KEY` | 선택 | FRED 거시지표 수집 |
| `FINNHUB_API_KEY` | 선택 | EPS 서프라이즈·내부자 거래 수집 |
| `NEWSAPI_KEY` | 선택 | 뉴스 기사 수집 |
| `ALPHAVANTAGE_API_KEY` | 선택 | EPS 심화·연간 손익 수집 |
| `FINANCIAL_AI_MODEL` | 선택 | `config.yaml`의 `llm.model`을 런타임에 덮어씀 |
| `FINANCIAL_AI_USER_AGENT` | 선택 | LLM API 요청 시 User-Agent 헤더 (게이트웨이 WAF 우회용) |

---

## 14. 디렉터리 구조

```
financial-ai/
├── app.py                      # Streamlit 대시보드 (4페이지)
├── start.sh                    # streamlit run app.py --server.port 8501
├── config.yaml                 # LLM·수집·평가·경로 전체 설정
├── requirements.txt
├── tickers.txt                 # 배치 실행용 종목 목록 예시
│
├── scripts/
│   ├── run_pipeline.py         # ★ 단일 진입점 (모든 실행은 여기서)
│   └── list_gateway_models.py  # 게이트웨이 모델 목록 단독 조회
│
├── prompts/
│   ├── report.j2               # LLM 리포트 생성 Jinja2 템플릿 (시스템+유저 메시지)
│   └── judge.j2                # LLM Judge 채점 Jinja2 템플릿
│
├── src/
│   ├── agents/                 # 멀티 에이전트 구조
│   │   ├── orchestrator.py     # 5개 Agent 순차 실행 조율
│   │   ├── base.py
│   │   ├── collect_agent.py    # Step 1: 6개 소스 수집
│   │   ├── analyze_agent.py    # Step 2: 퀀트 피처 계산 + 컨텍스트 구성
│   │   ├── report_agent.py     # Step 3: LLM 리포트 생성
│   │   ├── eval_agent.py       # Step 4: 규칙(M0) + Judge(M2) 평가
│   │   └── signal_agent.py     # Step 5: 신호 추출 + CSV 기록
│   │
│   ├── ingest/                 # 데이터 수집 모듈
│   │   ├── yahoo.py            # Yahoo Finance (yfinance)
│   │   ├── edgar.py            # SEC EDGAR 10-K
│   │   ├── fred.py             # FRED 거시지표
│   │   ├── finnhub.py          # Finnhub EPS·내부자거래
│   │   ├── newsapi.py          # NewsAPI 뉴스·감성
│   │   └── alphavantage.py     # Alpha Vantage EPS·재무
│   │
│   ├── features/
│   │   └── builder.py          # 퀀트 5팩터 점수 계산 (FeatureBuilder)
│   │
│   ├── report/
│   │   ├── composer.py         # ContextBuilder + Jinja2 렌더링
│   │   └── llm.py              # LLMProvider (OpenAI / Anthropic) + 게이트웨이 조회
│   │
│   ├── eval/
│   │   ├── rules.py            # M0 규칙 자동 채점 (출처·리스크·목표가·페널티)
│   │   ├── rubric.py           # M0/M2 집계 + 등급 판정
│   │   ├── judge.py            # LLM Judge 프롬프트 렌더링·파싱
│   │   └── number_scan.py      # 리포트 내 숫자 패턴 탐지
│   │
│   ├── trading_stub/
│   │   └── signal.py           # 신호 추출·퀀트 결합·신뢰도 산정
│   │
│   ├── backtest/
│   │   └── runner.py           # BacktestRunner (과거 퀀트 재계산 + forward return)
│   │
│   └── fio/
│       └── storage.py          # JSON 읽기/쓰기, CSV append 유틸
│
├── artifacts/                  # 실행 산출물 (git 제외)
│   ├── <TICKER>/
│   │   └── <YYYY-MM-DD>/
│   │       ├── raw/
│   │       │   ├── yfinance.json
│   │       │   ├── edgar.json
│   │       │   ├── fred.json
│   │       │   ├── finnhub.json
│   │       │   ├── newsapi.json
│   │       │   └── alphavantage.json
│   │       ├── snapshot.json   # 6개 소스 합본
│   │       ├── context.json    # LLM 입력 컨텍스트
│   │       ├── eval.json       # 루브릭 채점 결과
│   │       └── signal.json     # 최종 매매 신호
│   └── _batch/
│       └── <YYYY-MM-DD>/
│           ├── batch_summary.json
│           └── errors.json     # 실패 종목 있을 경우에만 생성
│
├── reports/                    # LLM 생성 Markdown 리포트
│   └── <TICKER>/
│       └── <YYYY-MM-DD>.md
│
├── tracking/
│   └── prediction_log.csv      # 누적 예측 이력 (성과 검증용)
│
└── logs/
    ├── available_models_latest.txt
    └── available_models_<YYYY-MM-DD>.txt
```

---

## 15. 문제 해결

### `403` / `"error code: 1010"` (게이트웨이 차단)
일부 게이트웨이가 비브라우저 클라이언트를 차단합니다. `report/llm.py`는 기본적으로 Chrome User-Agent를 사용하며, 환경변수 `FINANCIAL_AI_USER_AGENT`로 변경할 수 있습니다.

### `permission_denied - No access to Model '…'`
해당 API 키로 해당 모델에 접근 권한이 없습니다. `--list-models`로 실제 접근 가능한 모델 ID를 확인하고 `config.yaml`의 `llm.model`을 변경하세요.

### LLM 키가 없다는 RuntimeError
`.env` 파일 위치를 확인하세요. `financial-ai/.env` 또는 `financial-ai/api_guide/.env` 중 하나에 `OPENAI_API_KEY=...`를 입력해야 합니다.

### 선택 소스 데이터가 전부 비어 있음
FRED·Finnhub·NewsAPI·Alpha Vantage는 API 키 없이도 실행되지만 해당 섹션은 "데이터 미제공"으로 표기됩니다. 리포트 품질 향상을 위해 각 소스의 무료 키 발급을 권장합니다.

### 한국 종목 재무 필드 누락
Yahoo Finance가 한국 종목(`.KS`)의 재무 데이터를 부분적으로만 제공합니다. 퀀트 점수 중 데이터 미가용 팩터는 0점 처리됩니다.

### 퀀트 점수와 LLM 신호가 완전히 반대 (buy vs sell)
`signal.py`의 reconcile 로직에 의해 자동으로 `hold` 처리되며 신뢰도는 0.35로 고정됩니다. 리포트 본문에서 어느 쪽 근거가 더 타당한지 직접 확인하세요.
