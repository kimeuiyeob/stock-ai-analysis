---
name: signal target price and stop loss logic
description: 목표가 검증 3단계, ATR 기반 손절가, ETF 차단 로직 — 실제 구현 기준
type: project
originSessionId: 83b611bd-404a-4984-abd0-3e817e19a091
---
## 구현 위치

- `src/trading_stub/signal.py` — `extract_signal_from_report()`, `_validate_target_price()`, `_atr_stop_loss()`
- `src/agents/collect_agent.py` — ETF/지원불가 종목 차단

---

## 목표가 산출 흐름

1. LLM 리포트의 마지막 ```json 블록에서 `target_price` 추출
2. 추출 실패 시 텍스트 정규식으로 `투자의견` 파싱 (target_price는 None 처리)

### 3단계 검증 (`_validate_target_price`)

| 단계 | 조건 | 처리 |
|------|------|------|
| ① 방향 | buy인데 목표가 < 현재가×0.98 | 폐기 → 애널리스트 평균으로 대체 |
| ② 범위 | 현재가 ×0.40 미만 or ×3.00 초과 | 폐기 → 애널리스트 평균으로 대체 |
| ③ 컨센서스 | 월가 평균과 60% 초과 괴리 | 블렌딩: `(llm_target + analyst_mean) / 2` |

LLM 목표가 없거나 검증 실패 시 → 월가 애널리스트 평균 컨센서스(`analyst_targets.mean`) 사용.

---

## 손절가 (`_atr_stop_loss`)

```python
손절가 = 현재가 - ATR(14일) × 2.0
```

- ATR 없으면 기본값: `현재가 × 0.82` (−18%)
- **buy 신호에만 적용** (hold/sell은 손절가 없음)
- **Why ATR×2**: 일반적인 추세추종 손절 기준. 변동성이 클수록 자동으로 폭 넓어짐.

ATR은 `src/ingest/yahoo.py` `_compute_atr()` — 14일 평균 True Range.

---

## ETF·지원불가 종목 차단

**위치**: `src/agents/collect_agent.py` — yfinance 수집 직후 (병렬 배치 시작 전)

```python
_UNSUPPORTED = {"ETF", "MUTUALFUND", "CRYPTOCURRENCY", "FUTURE", "INDEX"}
quote_type = yf_data.get("info", {}).get("quoteType", "").upper()
if quote_type in _UNSUPPORTED:
    raise ValueError("[지원불가 종목] ...")
```

**Why 이 위치**: yfinance가 1단계에서 단독 실행됨. 여기서 차단하면 나머지 5개 소스 병렬 수집을 시작도 안 함 → 불필요한 API 비용·시간 낭비 없음.

`quoteType`을 yfinance에서 가져오려면 `src/ingest/yahoo.py` `keys_info` 리스트에 `"quoteType"` 포함 필수 (이미 포함됨).

---

## app.py UI — 에러 표시 정책

- **단일 티커, 알려진 에러** (`[지원불가 종목]`, 가격 이력 없음): `st.error()`만 표시, 로그 코드블록 없음
- **단일 티커, 미분류 에러**: `st.code()` 로그 + `st.error("오류가 발생했습니다")`
- **배치 (다중 티커)**: 항상 `st.code()` 로그 + 성공/실패 요약 테이블

---

## app.py UI — 폼 재활성화 패턴

파이프라인 완료 후 입력 폼이 비활성 상태로 남는 문제 해결:

```python
@st.fragment(run_every=1 if pl["running"] else None)
def _log_panel():
    if not _pl["running"] and rc is not None and not _pl.get("rerun_done"):
        _pl["rerun_done"] = True
        st.rerun()  # fragment 안에서 st.rerun() → 전체 페이지 rerun
```

`rerun_done` 플래그 없으면 무한 rerun 루프 발생.  
`run_every=None` 으로 설정하면 fragment 자동 갱신 중단.
