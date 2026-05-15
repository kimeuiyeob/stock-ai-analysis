from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass
class TradingSignal:
    ticker: str
    signal: Literal["buy", "hold", "sell"]
    confidence: float
    time_horizon: Literal["1m", "3m", "6m", "12m"]
    thesis_bullets: list[str]
    risk_triggers: list[str]
    source_report: str
    date: str
    target_price: float | None = None           # AI가 재무 분석으로 산출한 목표가
    analyst_target_price: float | None = None   # 월가 애널리스트 컨센서스 목표가
    stop_loss: float | None = None
    quant_score: int | None = None
    quant_breakdown: dict | None = field(default=None)


def today_str() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _extract_section_bullets(report_text: str, section_keyword: str) -> list[str]:
    in_section = False
    bullets: list[str] = []
    for raw in report_text.splitlines():
        line = raw.strip()
        if re.match(r"^#{1,6}\s+", line):
            if in_section:
                break
            if section_keyword.lower() in line.lower():
                in_section = True
            continue
        if not in_section:
            continue

        item: str | None = None
        if len(line) >= 2 and line[0] in ("-", "*", "•") and line[1].isspace():
            item = line[2:].strip()
        else:
            mnum = re.match(r"^(\d+)\.\s+(.*)$", line)
            if mnum:
                item = (mnum.group(2) or "").strip()
        if not item:
            continue

        item = re.sub(r"\*\*(.*?)\*\*", r"\1", item).strip()
        item = re.sub(r"\*(.*?)\*", r"\1", item).strip()
        if item:
            bullets.append(item)

    trimmed = [b[:280].rstrip() for b in bullets if b]
    return trimmed[:5]


def _extract_structured_signal(report_text: str) -> dict[str, Any] | None:
    """리포트 첫 번째 유효한 ```json 신호 블록에서 구조화 신호를 추출한다."""
    matches = list(re.finditer(r"```json\s*(\{.*?\})\s*```", report_text, re.S))
    for m in matches:
        try:
            data = json.loads(m.group(1))
            if isinstance(data.get("signal"), str) and data["signal"].lower() in ("buy", "hold", "sell"):
                return data
        except (json.JSONDecodeError, ValueError):
            continue
    return None



def _validate_ai_target_price(
    llm_target: float | None,
    current_price: float,
    signal: str,
) -> tuple[float | None, str | None]:
    """AI 목표가 기본 검증. 실패 시 None 반환 (애널리스트값 대체 없음)."""
    if llm_target is None or current_price <= 0:
        return None, "AI 목표가 없음"

    # 신호 방향 불일치
    if signal == "buy" and llm_target < current_price * 0.98:
        return None, f"buy 신호이나 목표가({llm_target:.1f})가 현재가({current_price:.1f}) 이하"
    if signal == "sell" and llm_target > current_price * 1.02:
        return None, f"sell 신호이나 목표가({llm_target:.1f})가 현재가({current_price:.1f}) 이상"

    # 이상값 범위 (현재가 대비 -60% ~ +200% 초과)
    if not (current_price * 0.40 <= llm_target <= current_price * 3.00):
        return None, f"목표가({llm_target:.1f})가 허용 범위 이탈"

    return llm_target, None


def _atr_stop_loss(
    current_price: float,
    atr_14: float | None,
    signal: str,
    multiplier: float = 2.0,
) -> tuple[float | None, str]:
    """ATR 기반 손절가 계산. 매수 신호에만 적용, 매도/중립은 None 반환."""
    if signal != "buy":
        return None, f"{signal} 신호 — 손절가 없음"
    if atr_14 and atr_14 > 0:
        sl = round(current_price - multiplier * atr_14, 2)
        pct = (sl - current_price) / current_price * 100
        return sl, f"ATR({atr_14:.2f}) × {multiplier} → 손절가 {sl:.2f} ({pct:.1f}%)"
    default_sl = round(current_price * 0.82, 2)
    return default_sl, "ATR 없음 → 현재가 -18% 기본값"


def extract_signal_from_report(
    report_text: str,
    eval_result: dict[str, Any],
    ticker: str,
    report_path: str,
    report_date: str,
    *,
    quant_score: dict[str, Any] | None = None,
    current_price: float | None = None,
    analyst_mean: float | None = None,
    atr_14: float | None = None,
) -> TradingSignal:
    # ── Step 1: 구조화 JSON 추출 ──────────────────────────────────────
    structured = _extract_structured_signal(report_text)
    llm_signal: str | None = None
    llm_target: float | None = None
    llm_stop: float | None = None
    llm_horizon: str | None = None

    llm_confidence: float | None = None

    if structured:
        raw_sig = (structured.get("signal") or "").lower()
        if raw_sig in ("buy", "hold", "sell"):
            llm_signal = raw_sig
        try:
            conf = structured.get("confidence")
            if conf is not None:
                llm_confidence = max(0.30, min(0.95, float(conf)))
        except (TypeError, ValueError):
            pass
        try:
            tp = structured.get("target_price")
            llm_target = float(tp) if tp is not None else None
        except (TypeError, ValueError):
            pass
        try:
            sl = structured.get("stop_loss")
            llm_stop = float(sl) if sl is not None else None
        except (TypeError, ValueError):
            pass
        raw_h = structured.get("horizon", "")
        if raw_h in ("1m", "3m", "6m", "12m"):
            llm_horizon = raw_h

    # 구조화 실패 시 텍스트 정규식 fallback
    if llm_signal is None:
        m = re.search(r"투자\s*의견[^\n]*?(매수|중립|매도)", report_text)
        opinion_map = {"매수": "buy", "중립": "hold", "매도": "sell"}
        if m:
            llm_signal = opinion_map.get(m.group(1))

    # ── Step 2: LLM 신호·신뢰도 확정 ─────────────────────────────────
    # LLM이 데이터만 보고 직접 판단한 결과를 그대로 사용
    final_signal = llm_signal or "hold"
    if llm_confidence is not None:
        final_confidence = llm_confidence
    else:
        # LLM이 confidence를 출력하지 않은 경우 eval 점수로 fallback
        basis = eval_result.get("score_normalized_100")
        final_confidence = (
            min(float(basis) / 100.0, 0.95)
            if basis is not None
            else min(float(eval_result.get("total_score", 50)) / 100.0, 0.95)
        )

    # ── Step 3: 투자 기간 ────────────────────────────────────────────
    if llm_horizon:
        horizon = llm_horizon
    else:
        hm = re.search(r"(12개월|6개월|3개월|1개월)", report_text)
        horizon_map = {"12개월": "12m", "6개월": "6m", "3개월": "3m", "1개월": "1m"}
        horizon = horizon_map.get(hm.group(1), "12m") if hm else "12m"

    # ── Step 4: 목표가·손절가 처리 ───────────────────────────────────
    cp = current_price or 0.0
    ai_target: float | None = None
    validated_stop: float | None = llm_stop

    if cp > 0:
        # AI 목표가: 기본 검증만, 실패 시 None (애널리스트값으로 대체 안 함)
        ai_target, tp_warn = _validate_ai_target_price(llm_target, cp, final_signal)
        if tp_warn:
            print(f"[signal] ⚠️  AI 목표가: {tp_warn}")

        # 손절가: ATR 기반으로 새로 계산 (LLM 값 무시)
        validated_stop, sl_msg = _atr_stop_loss(cp, atr_14, final_signal)
        print(f"[signal]    손절가: {sl_msg}")

    # 애널리스트 목표가: 원본 그대로 저장
    analyst_target = round(float(analyst_mean), 2) if analyst_mean else None

    bullets = _extract_section_bullets(report_text, "성장 동력")
    risks = _extract_section_bullets(report_text, "리스크 요인")

    return TradingSignal(
        ticker=ticker,
        signal=final_signal,
        confidence=round(final_confidence, 4),
        time_horizon=horizon,
        thesis_bullets=bullets[:5],
        risk_triggers=risks[:5],
        source_report=report_path,
        date=report_date,
        target_price=ai_target,
        analyst_target_price=analyst_target,
        stop_loss=validated_stop,
        quant_score=quant_score.get("score") if quant_score else None,
        quant_breakdown=quant_score.get("breakdown") if quant_score else None,
    )


def to_backtest_input(signal: TradingSignal) -> dict[str, Any]:
    horizon_days = {"1m": 30, "3m": 90, "6m": 180, "12m": 365}
    return {
        "date": signal.date,
        "ticker": signal.ticker,
        "direction": signal.signal,
        "weight": signal.confidence,
        "horizon_days": horizon_days.get(signal.time_horizon, 365),
        "target_price": signal.target_price,
        "stop_loss": signal.stop_loss,
        "thesis": signal.thesis_bullets,
        "risk_triggers": signal.risk_triggers,
    }


def trading_signal_to_json(sig: TradingSignal) -> dict[str, Any]:
    return asdict(sig)
