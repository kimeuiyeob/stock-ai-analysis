---
name: quant scoring system design decisions
description: 퀀트 점수 100점 세부 채점 로직, 섹터 보정, 데이터 완성도 조정 — 모든 설계 결정과 이유
type: project
originSessionId: 83b611bd-404a-4984-abd0-3e817e19a091
---
## 구현 위치

- 채점: `src/features/builder.py` — `_compute_quant_score()`
- 완성도 보정: `src/agents/signal_agent.py` — run() 상단

---

## FACTOR 1 | VALUATION — 20점

**EV/EBITDA (10점)** — Greenblatt Magic Formula 핵심 지표. 자본구조·세율 왜곡 없음.

| 일반 섹터 | 유틸리티·에너지 |
|-----------|----------------|
| ≤8배→10점 | ≤12배→10점 |
| ≤12배→8점 | ≤18배→8점 |
| ≤16배→6점 | ≤25배→6점 |
| ≤22배→3점 | ≤35배→3점 |

**Why 유틸리티·에너지 완화**: 인프라 투자 특성상 EBITDA 배수가 높은 게 정상. 일반 기준 적용 시 JPM에서 나쁜 점수가 나오는 등 섹터 blind spot 발생.

**FCF 수익률 (6점)** — >7%→6점 / >4%→5점 / >2%→3점 / >0%→1점

**Forward PER (4점)** — ≤12배→4점 / ≤17배→3점 / ≤22배→2점 / ≤30배→1점

---

## FACTOR 2 | QUALITY — 25점

Novy-Marx(2013) + Fama-French RMW 기반.

- **총이익률 (5점)**: >55%→5점 / >35%→3점 / >15%→1점
- **영업이익률 (8점)**: >25%→8점 / >15%→6점 / >8%→4점 / >0%→2점
- **ROE (7점)**: >25%→7점 / >15%→5점 / >8%→3점 / >0%→1점
- **ROA (5점)**: >15%→5점 / >8%→4점 / >4%→2점 / >0%→1점

---

## FACTOR 3 | MOMENTUM — 20점

Jegadeesh & Titman(1993) — 최근 1개월 제외(단기 역전 회피).

- **12-1개월 수익률 (12점)**: >30%→12점 / >15%→9점 / >5%→6점 / >0%→3점
- **6-1개월 수익률 (8점)**: >15%→8점 / >5%→6점 / >0%→3점

---

## FACTOR 4 | FINANCIAL HEALTH — 20점

- **순부채/FCF 상환연수 (8점)**: 순현금→8점 / ≤2년→6점 / ≤4년→4점 / ≤7년→2점
- **Debt/Equity (7점)**: ≤20%→7점 / ≤50%→5점 / ≤100%→3점 / ≤200%→1점
- **FCF 품질 (5점)**: FCF>0→+3점, FCF≥순이익×80%→+2점

**섹터 보정 (금융·부동산)**:
- D/E와 순부채/FCF 항목을 중립값(각각 3점, 4점)으로 강제 처리
- **Why**: 금융·부동산은 레버리지가 사업 구조상 필수. 일반 기준 적용 시 JP Morgan, Simon Property 같은 우량 종목이 부당하게 낮은 점수 획득.

---

## FACTOR 5 | GROWTH — 15점

- **매출 YoY (9점)**: >25%→9점 / >15%→7점 / >7%→4점 / >0%→1점
- **EPS 성장률 (6점)**: >25%→6점 / >10%→4점 / >0%→2점

**Why 애널리스트 목표가 제거**: 원래 애널리스트 upside를 Growth 팩터에 포함했으나, LLM이 목표가를 리포트에 직접 쓰기 때문에 LLM 신뢰도 → 퀀트 점수 → 다시 LLM 리포트 평가로 순환 참조 발생. 완전히 제거.

---

## 데이터 완성도 (data_completeness)

`_avail: list[bool]` — 각 팩터가 실제 데이터로 채점됐는지 추적.

- 데이터 없는 항목 → `max_pts // 2` (만점의 절반, 중립)
- `completeness = sum(_avail) / len(_avail)`
- **SignalAgent에서 퀀트 점수 보정**: `adjusted = raw_score × completeness + 50 × (1 - completeness)`
  - completeness=1.0 → 원점수 유지
  - completeness=0.5 → 원점수와 50의 중간
  - **Why**: 데이터 없을수록 중립(50)에 가깝게 당김 → 부정확한 데이터가 강한 매수/매도 신호를 만드는 것 방지

---

## Confidence 계산 (builder.py 말단)

```python
if total >= 60:  # buy
    confidence = 0.50 + (total - 60) / 40 * 0.45  # 60점→0.50, 100점→0.95
elif total < 40:  # sell
    confidence = 0.50 + (40 - total) / 40 * 0.45  # 40점→0.50, 0점→0.95
else:  # hold
    confidence = 0.65 - abs(total - 50) / 10 * 0.15  # 50점→0.65, 40/60점→0.50
```

**Why 이전 방식(score/100) 문제점**:
- buy 100점 → confidence=1.00 (비현실적)
- hold 구간 항상 0.50 고정 (50점과 41점이 동일)
- 경계 절벽: 59점 hold(0.50) → 60점 buy(0.60) 급등

---

## LLM+퀀트 신호 결합 (signal.py `_reconcile_signals`)

1. LLM == 퀀트: `min(0.95, quant_confidence × 1.15)`
2. 한쪽이 hold: `max(0.35, quant_confidence × 0.70)`
3. 완전 반대 (buy vs sell): hold로 처리, confidence=0.35 고정
