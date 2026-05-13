"""Financial AI — Streamlit 대시보드."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent
ARTIFACTS = ROOT / "artifacts"
REPORTS = ROOT / "reports"
TRACKING_CSV = ROOT / "tracking" / "prediction_log.csv"

st.set_page_config(page_title="Financial AI", page_icon="📈", layout="wide")

st.markdown("""
<style>
/* metric 값 글자 살짝 축소 */
[data-testid="stMetricValue"] {
    font-size: 1.6rem !important;
}

/* selectbox 테두리 */
[data-testid="stSelectbox"] > div > div {
    border: 1px solid rgba(128, 128, 128, 0.4) !important;
    border-radius: 6px !important;
}
[data-testid="stSelectbox"] > div > div:focus-within {
    border-color: rgba(128, 128, 128, 0.8) !important;
}

@media (max-width: 768px) {
    /* 페이지 좌우 여백 축소 */
    .block-container {
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
        padding-top: 1rem !important;
    }
    /* 컬럼 세로 쌓기 */
    [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
    }
    [data-testid="column"] {
        min-width: 100% !important;
        width: 100% !important;
    }
    /* 버튼 터치 영역 확대 */
    .stButton > button {
        width: 100% !important;
        min-height: 2.75rem !important;
        font-size: 1rem !important;
    }
    /* 메트릭 가독성 */
    [data-testid="stMetricValue"] {
        font-size: 1.3rem !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.8rem !important;
    }
    /* 텍스트 입력 터치 최적화 */
    .stTextInput input {
        font-size: 1rem !important;
        min-height: 2.5rem !important;
    }
    /* 로그 코드블록 가로 스크롤 */
    pre {
        font-size: 0.72rem !important;
        overflow-x: auto !important;
    }
    /* 데이터프레임 가로 스크롤 허용 */
    [data-testid="stDataFrame"] {
        overflow-x: auto !important;
    }
    /* 구분선 여백 */
    hr {
        margin: 0.5rem 0 !important;
    }
}
</style>
""", unsafe_allow_html=True)

# ── 공통 헬퍼 ────────────────────────────────────────────────────────

def _signal_icon(signal: str) -> str:
    return {"buy": "🟢", "hold": "🟡", "sell": "🔴"}.get((signal or "").lower(), "⚪")


def _fmt_price(p: float) -> str:
    if p >= 1:
        return f"${p:,.0f}"
    return f"${p:.2f}"


def _signal_badge(signal: str) -> str:
    colors = {"buy": "#1a7a1a", "hold": "#9a7800", "sell": "#a01010"}
    s = (signal or "").lower()
    bg = colors.get(s, "#555")
    label = {"buy": "매수", "hold": "중립", "sell": "매도"}.get(s, s.upper())
    return f'<span style="background:{bg};color:#fff;padding:2px 10px;border-radius:12px;font-weight:bold">{label}</span>'


def load_latest_signals() -> list[dict]:
    """artifacts/ 에서 티커별 최신 signal.json 로드."""
    result = []
    if not ARTIFACTS.exists():
        return result
    for ticker_dir in sorted(ARTIFACTS.iterdir()):
        if not ticker_dir.is_dir() or ticker_dir.name.startswith("_"):
            continue
        date_dirs = sorted([d for d in ticker_dir.iterdir() if d.is_dir()])
        if not date_dirs:
            continue
        latest = date_dirs[-1]
        sig_file = latest / "signal.json"
        eval_file = latest / "eval.json"
        if not sig_file.exists():
            continue
        try:
            sig = json.loads(sig_file.read_text(encoding="utf-8"))
            ev = json.loads(eval_file.read_text(encoding="utf-8")) if eval_file.exists() else {}
            snap_file = latest / "snapshot.json"
            current_price = None
            if snap_file.exists():
                try:
                    snap = json.loads(snap_file.read_text(encoding="utf-8"))
                    current_price = snap.get("price", {}).get("current")
                except Exception:
                    pass
            result.append({
                "ticker": sig.get("ticker", ticker_dir.name),
                "date": latest.name,
                "signal": sig.get("signal", "-"),
                "confidence": sig.get("confidence", 0),
                "current_price": current_price,
                "quant_score": sig.get("quant_score"),
                "target_price": sig.get("target_price"),
                "stop_loss": sig.get("stop_loss"),
                "time_horizon": sig.get("time_horizon", "-"),
                "eval_score": ev.get("total_score"),
                "eval_grade": ev.get("grade", ""),
                "breakdown": sig.get("quant_breakdown") or {},
            })
        except Exception:
            continue
    return result


def available_reports() -> dict[str, list[str]]:
    """ticker → [날짜, ...] 내림차순 목록."""
    out: dict[str, list[str]] = {}
    if not REPORTS.exists():
        return out
    for d in sorted(REPORTS.iterdir()):
        if not d.is_dir():
            continue
        dates = sorted([f.stem for f in d.glob("*.md")], reverse=True)
        if dates:
            out[d.name] = dates
    return out


# ── 파이프라인 상태 (페이지 무관하게 최상단에서 초기화) ──────────────
if "pl" not in st.session_state:
    st.session_state.pl = {
        "running": False,
        "logs": [],
        "returncode": None,
        "tickers": "",
        "rerun_done": False,  # 완료 시 rerun 중복 방지
    }
pl = st.session_state.pl

# ── 사이드바 네비게이션 ──────────────────────────────────────────────

with st.sidebar:
    st.title("📈 Financial AI")
    st.markdown("---")
    page = st.radio(
        "메뉴",
        ["📊 대시보드", "📄 리포트", "🚀 주식 분석", "📈 예측 이력"],
        label_visibility="collapsed",
    )
    if pl["running"]:
        st.markdown("---")
        st.info(f"⏳ 분석 중: `{pl['tickers']}`")


# ════════════════════════════════════════════════════════════════════
# 📊 대시보드
# ════════════════════════════════════════════════════════════════════
if page == "📊 대시보드":
    st.title("📊 대시보드")

    signals = load_latest_signals()
    if not signals:
        st.info("분석된 종목이 없습니다. 파이프라인을 먼저 실행하세요.")
        st.stop()

    # 요약 집계
    buy_n = sum(1 for s in signals if s["signal"] == "buy")
    hold_n = sum(1 for s in signals if s["signal"] == "hold")
    sell_n = sum(1 for s in signals if s["signal"] == "sell")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("전체 종목", len(signals))
    c2.metric("🟢 매수", buy_n)
    c3.metric("🟡 중립", hold_n)
    c4.metric("🔴 매도", sell_n)

    st.markdown("---")

    # 종목 목차
    toc_items = []
    for sig in signals:
        icon = _signal_icon(sig["signal"])
        anchor = f"{sig['ticker'].lower()}-{sig['date']}"
        toc_items.append(
            f'<a href="#{anchor}" style="text-decoration:none;color:inherit;font-weight:bold">'
            f'{icon} {sig["ticker"]}</a>'
        )
    st.markdown(" &nbsp;&nbsp; ".join(toc_items), unsafe_allow_html=True)
    st.markdown("---")

    # 종목 카드 (3열)
    for row_start in range(0, len(signals), 3):
        row_sigs = signals[row_start: row_start + 3]
        cols = st.columns(3)
        for col, sig in zip(cols, row_sigs):
            with col:
                icon = _signal_icon(sig["signal"])
                st.markdown(
                    f"### {icon} {sig['ticker']} "
                    f'<span style="font-size:0.75rem;color:#888">{sig["date"]}</span>',
                    unsafe_allow_html=True,
                )
                st.markdown(_signal_badge(sig["signal"]), unsafe_allow_html=True)
                st.write("")

                m1, m2 = st.columns(2)
                m1.metric("신뢰도", f"{sig['confidence']:.1%}",
                    help=(
                        "퀀트 점수 기반 기준 신뢰도:\n"
                        "  • 매수(60 ~ 100점): 0.50 → 0.95 선형 스케일\n"
                        "  • 매도(0 ~ 40점): 0.50 → 0.95 선형 스케일\n"
                        "  • 중립(40 ~ 60점): 50점=0.65, 경계=0.50\n"
                        "LLM·퀀트 신호 일치 → ×1.15 (최대 0.95)\n"
                        "한쪽이 중립 → ×0.70 (최소 0.35)\n"
                        "완전 반대(매수↔매도) → 0.35 고정"
                    ))
                if sig["quant_score"] is not None:
                    m2.metric("퀀트", f"{sig['quant_score']}/100",
                        help=(
                            "밸류에이션(20)+퀄리티(25)+모멘텀(20)+재무건전성(20)+성장성(15) 합산\n"
                            "≥60점 → 매수 / 40 ~ 59점 → 중립 / <40점 → 매도\n"
                            "데이터 누락 항목은 만점의 절반(중립값)으로 처리됩니다."
                        ))

                if sig["quant_score"] is not None:
                    st.progress(
                        sig["quant_score"] / 100,
                        text=f"퀀트 점수 {sig['quant_score']}점",
                    )

                p1, p2, p3 = st.columns(3)
                upside = None
                if sig["target_price"] and sig["current_price"]:
                    upside = (sig["target_price"] / sig["current_price"] - 1) * 100
                if sig["current_price"]:
                    p1.metric(
                        "현재가", _fmt_price(sig["current_price"]),
                        delta=f"{upside:+.0f}%" if upside is not None else None,
                        help=(
                            "분석 시점의 시장 거래가입니다.\n"
                            "△ 수치는 목표가 대비 예상 상승여력을 나타냅니다."
                        ),
                    )
                if sig["target_price"]:
                    p2.metric(
                        "목표가", _fmt_price(sig["target_price"]),
                        help=(
                            "AI 리포트에서 추출한 12개월 기준 목표주가입니다.\n"
                            "① 방향 검증 — 매수 신호인데 목표가가 현재가 이하면 폐기\n"
                            "② 범위 검증 — 현재가 대비 −60%~+200% 이탈 시 폐기\n"
                            "③ 컨센서스 대조 — 월가 평균과 60% 이상 괴리 시 두 값의 평균으로 자동 보정\n"
                            "목표가 산출 불가 시 월가 애널리스트 평균 컨센서스로 대체됩니다."
                        ),
                    )
                if sig["stop_loss"]:
                    p3.metric(
                        "손절가", _fmt_price(sig["stop_loss"]),
                        help=(
                            "14일 ATR(평균 실질 변동폭) × 2배를 현재가에서 차감하여 산출합니다.\n"
                            "변동성이 클수록 손절 폭이 넓어지고, 안정적인 종목은 좁게 설정됩니다.\n"
                            "ATR 데이터 없을 경우 현재가 −18%를 기본값으로 사용합니다.\n"
                            "매수(buy) 신호에만 적용됩니다."
                        ),
                    )

                if sig["eval_score"] is not None:
                    st.caption(
                        f"리포트: {sig['eval_score']:.0f}점 · "
                        + (sig["eval_grade"].split(":")[0] if sig["eval_grade"] else "")
                    )

                # 퀀트 세부 점수 expander
                if sig["breakdown"]:
                    with st.expander("퀀트 세부 점수"):
                        bd = sig["breakdown"]
                        _QUANT_HELP = {
                            "밸류에이션": (
                                "만점 20점 | Greenblatt Magic Formula 기반\n"
                                "EV/EBITDA (10점): 자본구조·세율 왜곡 없는 절대 저평가 지표\n"
                                "  일반: ≤8배→10점 / ≤12배→8점 / ≤16배→6점 / ≤22배→3점\n"
                                "  유틸리티·에너지: ≤12배→10점 / ≤18배→8점 / ≤25배→6점 / ≤35배→3점\n"
                                "  (인프라 투자 특성상 배수가 높은 게 정상 — 기준 완화)\n"
                                "FCF 수익률 (6점): 주주 실질 현금 수익률\n"
                                "  >7%→6점 / >4%→5점 / >2%→3점 / >0%→1점\n"
                                "Forward PER (4점): 컨센서스 이익 기준 상대 저평가\n"
                                "  ≤12배→4점 / ≤17배→3점 / ≤22배→2점 / ≤30배→1점"
                            ),
                            "퀄리티": (
                                "만점 25점 | Novy-Marx(2013) + Fama-French RMW 팩터\n"
                                "총이익률 (5점): 가격결정력 — Novy-Marx의 핵심 품질 지표\n"
                                "  >55%→5점 / >35%→3점 / >15%→1점\n"
                                "영업이익률 (8점): 핵심 사업 경쟁력\n"
                                "  >25%→8점 / >15%→6점 / >8%→4점 / >0%→2점\n"
                                "ROE (7점): 자기자본 효율 — Fama-French RMW 대표 지표\n"
                                "  >25%→7점 / >15%→5점 / >8%→3점 / >0%→1점\n"
                                "ROA (5점): 총자산 효율 — 레버리지로 부풀린 ROE 보완\n"
                                "  >15%→5점 / >8%→4점 / >4%→2점 / >0%→1점"
                            ),
                            "모멘텀": (
                                "만점 20점 | Jegadeesh & Titman(1993) 검증 시그널\n"
                                "12-1개월 수익률 (12점): 핵심 — 최근 1개월 제외(단기 역전 회피)\n"
                                "  >30%→12점 / >15%→9점 / >5%→6점 / >0%→3점\n"
                                "6-1개월 수익률 (8점): 중기 모멘텀 확인\n"
                                "  >15%→8점 / >5%→6점 / >0%→3점"
                            ),
                            "재무건전성": (
                                "만점 20점 | Piotroski F-Score + Altman Z-Score 개념\n"
                                "순부채/FCF 상환연수 (8점): 실질 부채 상환 능력\n"
                                "  순현금→8점 / ≤2년→6점 / ≤4년→4점 / ≤7년→2점\n"
                                "Debt/Equity (7점): yfinance % 기준\n"
                                "  ≤20%→7점 / ≤50%→5점 / ≤100%→3점 / ≤200%→1점\n"
                                "FCF 품질 (5점): Piotroski 발생액 신호\n"
                                "  FCF>0→+3점, FCF≥순이익×80%→+2점(이익의 질 우수)\n"
                                "※ 금융·부동산 섹터는 D/E·순부채 항목 중립 처리\n"
                                "  (레버리지가 사업 특성상 높은 게 정상이므로 불이익 없음)"
                            ),
                            "성장성": (
                                "만점 15점\n"
                                "매출 YoY (9점): 재무제표 기반 연간 매출 성장률\n"
                                "  >25%→9점 / >15%→7점 / >7%→4점 / >0%→1점\n"
                                "EPS 성장률 (6점): yfinance earningsGrowth (YoY)\n"
                                "  >25%→6점 / >10%→4점 / >0%→2점\n"
                                "※ 데이터 누락 항목은 만점의 절반(중립값)으로 처리됩니다."
                            ),
                        }
                        rows = [
                            ("밸류에이션",  bd.get("valuation", 0), 20),
                            ("퀄리티",     bd.get("quality",   0), 25),
                            ("모멘텀",     bd.get("momentum",  0), 20),
                            ("재무건전성", bd.get("health",    0), 20),
                            ("성장성",     bd.get("growth",    0), 15),
                        ]
                        for name, score, cap in rows:
                            c_label, c_bar = st.columns([1, 2])
                            c_label.metric(
                                name, f"{score}/{cap}",
                                help=_QUANT_HELP.get(name, ""),
                            )
                            c_bar.write("")
                            c_bar.progress(max(0.0, min(1.0, score / cap)) if cap else 0)

                st.markdown("---")


# ════════════════════════════════════════════════════════════════════
# 📄 리포트
# ════════════════════════════════════════════════════════════════════
elif page == "📄 리포트":
    st.title("📄 리포트")

    report_map = available_reports()
    if not report_map:
        st.info("생성된 리포트가 없습니다.")
        st.stop()

    with st.sidebar:
        st.markdown("---")
        ticker = st.selectbox("종목", list(report_map.keys()))
        date = st.selectbox("날짜", report_map[ticker])

        eval_path = ARTIFACTS / ticker / date / "eval.json"
        sig_path = ARTIFACTS / ticker / date / "signal.json"

        if sig_path.exists():
            sig = json.loads(sig_path.read_text(encoding="utf-8"))
            snap_path = ARTIFACTS / ticker / date / "snapshot.json"
            current_price = None
            if snap_path.exists():
                try:
                    snap = json.loads(snap_path.read_text(encoding="utf-8"))
                    current_price = snap.get("price", {}).get("current")
                except Exception:
                    pass
            icon = _signal_icon(sig.get("signal", ""))
            st.markdown("### 신호")
            st.markdown(_signal_badge(sig.get("signal", "")), unsafe_allow_html=True)
            st.write("")
            if current_price:
                st.metric(
                    "현재가", f"${current_price:,.1f}",
                    help="분석 시점의 시장 거래가입니다.",
                )
            st.metric("신뢰도", f"{sig.get('confidence', 0):.1%}",
                help=(
                    "퀀트 점수 기반 기준 신뢰도:\n"
                    "  • 매수(60 ~ 100점): 0.50 → 0.95 선형 스케일\n"
                    "  • 매도(0 ~ 40점): 0.50 → 0.95 선형 스케일\n"
                    "  • 중립(40 ~ 60점): 50점=0.65, 경계=0.50\n"
                    "LLM·퀀트 신호 일치 → ×1.15 (최대 0.95)\n"
                    "한쪽이 중립 → ×0.70 (최소 0.35)\n"
                    "완전 반대(매수↔매도) → 0.35 고정"
                ))
            if sig.get("quant_score") is not None:
                st.metric("퀀트 점수", f"{sig['quant_score']}/100",
                    help=(
                        "밸류에이션(20)+퀄리티(25)+모멘텀(20)+재무건전성(20)+성장성(15) 합산\n"
                        "≥60점 → 매수 / 40 ~ 59점 → 중립 / <40점 → 매도\n"
                        "데이터 누락 항목은 만점의 절반(중립값)으로 처리됩니다."
                    ))
            if sig.get("target_price"):
                st.metric(
                    "목표가", f"${sig['target_price']:,.1f}",
                    help=(
                        "AI 리포트에서 추출한 12개월 기준 목표주가입니다.\n"
                        "① 방향 검증 — 매수 신호인데 목표가가 현재가 이하면 폐기\n"
                        "② 범위 검증 — 현재가 대비 −60%~+200% 이탈 시 폐기\n"
                        "③ 컨센서스 대조 — 월가 평균과 60% 이상 괴리 시 두 값의 평균으로 자동 보정\n"
                        "목표가 산출 불가 시 월가 애널리스트 평균 컨센서스로 대체됩니다."
                    ),
                )
            if sig.get("stop_loss"):
                st.metric(
                    "손절가", f"${sig['stop_loss']:,.1f}",
                    help=(
                        "14일 ATR(평균 실질 변동폭) × 2배를 현재가에서 차감하여 산출합니다.\n"
                        "변동성이 클수록 손절 폭이 넓어지고, 안정적인 종목은 좁게 설정됩니다.\n"
                        "ATR 데이터 없을 경우 현재가 −18%를 기본값으로 사용합니다.\n"
                        "매수(buy) 신호에만 적용됩니다."
                    ),
                )

        if eval_path.exists():
            ev = json.loads(eval_path.read_text(encoding="utf-8"))
            st.markdown("---")
            st.markdown("### 리포트 평가")
            st.metric("총점", f"{ev.get('total_score', '-'):.0f}/100")
            grade = ev.get("grade", "")
            if grade:
                st.caption(grade)
            flags = ev.get("flags", [])
            if flags:
                with st.expander(f"플래그 {len(flags)}건"):
                    for f in flags:
                        st.warning(f, icon="⚠️")

    report_path = REPORTS / ticker / f"{date}.md"
    if report_path.exists():
        st.markdown(report_path.read_text(encoding="utf-8"))
    else:
        st.warning("리포트 파일을 찾을 수 없습니다.")


# ════════════════════════════════════════════════════════════════════
# 🚀 주식 분석
# ════════════════════════════════════════════════════════════════════
elif page == "🚀 주식 분석":
    st.title("🚀 주식 분석")

    running = pl["running"]

    # ── 입력 폼 (실행 중 비활성화) ───────────────────────────────────
    with st.form("pipeline_form"):
        tickers_input = st.text_input(
            "티커 (쉼표 구분)",
            placeholder="예: AAPL, TSLA, NVDA",
            disabled=running,
        )
        c1, c2 = st.columns(2)
        use_judge = c1.checkbox("LLM Judge(M2) 사용", value=True, disabled=running)
        skip_llm = c2.checkbox("LLM 생략 (스텁)", value=False, disabled=running)
        submitted = st.form_submit_button(
            "⏳ 실행 중..." if running else "▶ 실행",
            type="primary",
            disabled=running,
        )

    # ── 새 실행 요청 ─────────────────────────────────────────────────
    if submitted and tickers_input.strip() and not running:
        tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]

        # 티커 형식 검증 — 영문자·숫자·점·하이픈만 허용 (1~10자)
        import re as _re
        invalid = [t for t in tickers if not _re.match(r'^[A-Z0-9.\-]{1,10}$', t)]
        if invalid:
            st.error(f"❌ 유효하지 않은 티커: `{', '.join(invalid)}`\n\n티커는 영문 대문자·숫자·점(.)·하이픈(-)만 사용할 수 있습니다. (예: AAPL, BRK.B, 005930.KS)")
            st.stop()

        cmd = [sys.executable, str(ROOT / "scripts" / "run_pipeline.py")]
        if len(tickers) == 1:
            cmd += ["--ticker", tickers[0]]
        else:
            cmd += ["--tickers", ",".join(tickers)]
        cmd.append("--judge" if use_judge else "--no-judge")
        if skip_llm:
            cmd.append("--skip-llm")

        pl["logs"] = []
        pl["returncode"] = None
        pl["running"] = True
        pl["tickers"] = ", ".join(tickers)
        pl["rerun_done"] = False

        def _bg(cmd, root, pl):
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(root),
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    pl["logs"].append(line)
            proc.wait()
            pl["returncode"] = proc.returncode
            pl["running"] = False

        threading.Thread(target=_bg, args=(cmd, ROOT, pl), daemon=True).start()
        st.rerun()

    elif submitted and not tickers_input.strip():
        st.warning("티커를 입력하세요.")

    # ── 로그 표시 (fragment로 격리 — 이 영역만 1초마다 갱신) ──────────
    @st.fragment(run_every=1 if pl["running"] else None)
    def _log_panel():
        _pl = st.session_state.pl
        if not _pl["logs"] and not _pl["running"]:
            return
        if _pl["tickers"]:
            st.markdown(f"**실행 종목**: `{_pl['tickers']}`")

        logs_text = "\n".join(_pl["logs"])

        is_batch = "," in _pl.get("tickers", "")
        rc = _pl["returncode"]

        # 단일 티커 알려진 에러 → 로그 없이 깔끔한 메시지만
        if not _pl["running"] and not is_batch and rc not in (None, 0):
            if "[지원불가 종목]" in logs_text:
                line = next((l for l in _pl["logs"] if "[지원불가 종목]" in l), "")
                st.error(line.split("[지원불가 종목]")[-1].strip())
            elif "가격 이력이 비어 있습니다" in logs_text or "no price data found" in logs_text or "possibly delisted" in logs_text or "Quote not found" in logs_text:
                st.error("티커를 찾을 수 없습니다. 올바른 티커인지 확인하세요. (예: AAPL, TSLA, NVDA, 005930.KS)")
            else:
                st.code("\n".join(_pl["logs"][-60:]), language=None)
                st.error("오류가 발생했습니다. 로그를 확인하세요.")
        else:
            # 실행 중 / 배치 / 성공 → 로그 표시
            st.code("\n".join(_pl["logs"][-60:]), language=None)
            if _pl["running"]:
                st.info("⏳ 실행 중... 다른 페이지를 둘러봐도 계속 진행됩니다.")
            elif is_batch:
                total = len(_pl["tickers"].split(","))
                failed_info: dict[str, str] = {}
                for line in _pl["logs"]:
                    if "[pipeline] [오류]" in line:
                        rest = line.split("[pipeline] [오류]")[-1].strip()
                        t, _, msg = rest.partition(":")
                        t, msg = t.strip(), msg.strip()
                        if "[지원불가 종목]" in msg:
                            msg = msg.split("[지원불가 종목]")[-1].strip().split(". ")[0] + "."
                        elif "가격 이력이 비어" in msg or "수집 실패" in msg or "Not Found" in msg:
                            msg = "티커를 찾을 수 없습니다."
                        failed_info[t] = msg
                success_n = total - len(failed_info)
                summary_md = (
                    f"**분석 티커** {total}개 &nbsp;|&nbsp; "
                    f"**성공** {success_n}개 &nbsp;|&nbsp; "
                    f"**실패** {len(failed_info)}개"
                )
                if not failed_info:
                    st.success(f"✅ {summary_md}")
                else:
                    st.warning(f"⚠️ {summary_md}")
                    fail_lines = "\n".join(f"- **{t}** — {r}" for t, r in failed_info.items())
                    st.markdown(f"**실패 내역**\n{fail_lines}")
            elif rc == 0:
                st.success("✅ 완료! 대시보드에서 결과를 확인하세요.")
            elif rc is not None:
                st.error("오류가 발생했습니다. 로그를 확인하세요.")

        if not _pl["running"] and rc is not None and not _pl.get("rerun_done"):
            _pl["rerun_done"] = True
            st.rerun()

    _log_panel()


# ════════════════════════════════════════════════════════════════════
# 📈 예측 이력
# ════════════════════════════════════════════════════════════════════
elif page == "📈 예측 이력":
    st.title("📈 예측 이력")

    if not TRACKING_CSV.exists():
        st.info("예측 이력이 없습니다. 파이프라인을 실행하면 자동으로 기록됩니다.")
        st.stop()

    df = pd.read_csv(TRACKING_CSV, on_bad_lines="skip")
    if df.empty:
        st.info("기록된 예측이 없습니다.")
        st.stop()

    # 필터
    c1, c2, c3 = st.columns(3)
    ticker_opts = sorted(df["ticker"].unique())
    tickers_sel = c1.multiselect("종목", ticker_opts, default=ticker_opts)
    signal_opts = ["buy", "hold", "sell"]
    signal_sel = c2.multiselect("신호", signal_opts, default=signal_opts)

    filtered = df[
        df["ticker"].isin(tickers_sel) & df["opinion"].isin(signal_sel)
    ].sort_values("date", ascending=False)

    c3.metric("필터된 기록", len(filtered))

    st.markdown("---")

    # 차트
    ch1, ch2 = st.columns(2)

    with ch1:
        st.subheader("신호 분포")
        sig_cnt = filtered["opinion"].value_counts().rename(
            {"buy": "매수", "hold": "중립", "sell": "매도"}
        )
        st.bar_chart(sig_cnt)

    with ch2:
        if "quant_score" in filtered.columns:
            st.subheader("종목별 퀀트 점수")
            qs = (
                filtered.groupby("ticker")["quant_score"]
                .mean()
                .dropna()
                .sort_values(ascending=False)
            )
            if not qs.empty:
                st.bar_chart(qs)

    st.markdown("---")

    # 상세 테이블
    st.subheader("전체 기록")
    display_cols = [
        c for c in [
            "date", "ticker", "price_at_report", "opinion",
            "target_price", "stop_loss", "confidence", "quant_score",
            "rubric_score", "horizon", "pe_actual",
        ] if c in filtered.columns
    ]

    st.dataframe(
        filtered[display_cols],
        hide_index=True,
        width="stretch",
        key="history_df",
        column_config={
            "opinion": st.column_config.TextColumn("신호"),
            "confidence": st.column_config.NumberColumn("신뢰도", format="%.2f"),
            "quant_score": st.column_config.ProgressColumn("퀀트", min_value=0, max_value=100),
            "price_at_report": st.column_config.NumberColumn("분석 시 가격", format="$%.2f"),
            "target_price": st.column_config.NumberColumn("목표가", format="$%.1f"),
            "stop_loss": st.column_config.NumberColumn("손절가", format="$%.1f"),
        },
    )
    st.caption(f"총 {len(filtered)}개 예측 기록")
