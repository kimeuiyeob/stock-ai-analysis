# Financial AI — Claude 컨텍스트

이 프로젝트에서 작업을 시작할 때 반드시 `docs/ai-context/` 폴더의 파일들을 읽어 컨텍스트를 파악하세요.

## 필수 참조 문서

- `docs/ai-context/project_financial_ai.md` — 전체 아키텍처, 파이프라인, 배포 정보
- `docs/ai-context/quant_scoring_design.md` — 퀀트 점수 100점 세부 로직, 섹터 보정, confidence 공식
- `docs/ai-context/signal_target_stoploss.md` — 목표가 검증, ATR 손절가, ETF 차단 로직, UI 패턴
- `docs/ai-context/ui_helptext_spec.md` — 대시보드 도움말 전문 (코드 변경 시 여기도 갱신)

## 중요 원칙

- `app.py` 도움말 수정 시 `docs/ai-context/ui_helptext_spec.md`도 함께 갱신
- 퀀트 점수 로직(`src/features/builder.py`) 변경 시 `docs/ai-context/quant_scoring_design.md`도 함께 갱신
- 파이프라인 구조 변경 시 `docs/ai-context/project_financial_ai.md`도 함께 갱신
- ETF/레버리지 상품은 CollectAgent 1단계에서 차단 (yfinance quoteType 기반)
- Streamlit 컬럼 레이아웃 모바일 대응은 불가 (Streamlit 한계, 재시도 불필요)
