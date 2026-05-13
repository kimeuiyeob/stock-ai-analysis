# Financial AI — AI Agent 가이드

이 프로젝트에서 작업 전 `docs/ai-context/` 폴더를 읽어 컨텍스트를 파악하세요.
개발자 온보딩은 `docs/ONBOARDING.md`를 참조하세요.

## 필수 참조 문서

- `docs/ai-context/project_financial_ai.md` — 전체 아키텍처, 파이프라인, 에이전트 구성, 배포 정보
- `docs/ai-context/quant_scoring_design.md` — 퀀트 점수 100점 세부 로직, 섹터 보정, confidence 공식
- `docs/ai-context/signal_target_stoploss.md` — 목표가 검증, ATR 손절가, ETF 차단 로직, UI 패턴
- `docs/ai-context/ui_helptext_spec.md` — 대시보드 도움말 전문 (코드 변경 시 여기도 갱신)

## 프로젝트 핵심 규칙

- 단일 진입점: `scripts/run_pipeline.py` — `src/` 모듈을 직접 실행하지 말 것
- `app.py` 도움말 수정 시 `docs/ai-context/ui_helptext_spec.md`도 함께 갱신
- 퀀트 점수 로직 변경 시 `docs/ai-context/quant_scoring_design.md`도 함께 갱신
- ETF/레버리지 상품은 CollectAgent 1단계에서 자동 차단됨 (재구현 불필요)
- Streamlit 컬럼 레이아웃 모바일 대응 불가 (Streamlit 한계, 재시도 불필요)
