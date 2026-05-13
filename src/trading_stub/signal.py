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
    target_price: float | None = None
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
    """리포트 마지막 ```json 블록에서 구조화 신호를 추출한다."""
    matches = list(re.finditer(r"```json\s*(\{.*?\})\s*```", report_text, re.S))
    for m in reversed(matches):
        try:
            data = json.loads(m.group(1))
            if isinstance(data.get("signal"), str) and data["signal"].lower() in ("buy", "hold", "sell"):
                return data
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _reconcile_signals(
    llm_signal: str | None,
    quant_signal: str,
    quant_confidence: float,
) -> tuple[str, float]:
    """LLM 신호와 퀀트 신호를 결합해 최종 방향·신뢰도를 결정한다."""
    if llm_signal is None:
        return quant_signal, quant_confidence

    if llm_signal == quant_signal:
        return llm_signal, min(0.95, round(quant_confidence * 1.15, 4))

    if llm_signal == "hold" or quant_signal == "hold":
        directional = llm_signal if quant_signal == "hold" else quant_signal
        return directional, max(0.35, round(quant_confidence * 0.70, 4))

    # 완전 반대(buy vs sell) → 불확실, hold로 처리
    return "hold", 0.35


def _validate_target_price(
    llm_target: float | None,
    current_price: float,
    analyst_mean: float | None,
    signal: str,
) -> tuple[float | None, str | None]:
    """LLM 목표가 검증. 문제 있으면 (보정값, 경고메시지) 반환."""
    if llm_target is None or current_price <= 0:
        return analyst_mean, "LLM 목표가 없음 → 애널리스트 컨센서스 사용"

    warning: str | None = None

    # 1. 신호 방향 일치 확인
    if signal == "buy" and llm_target < current_price * 0.98:
        warning = f"buy 신호이나 목표가({llm_target:.1f})가 현재가({current_price:.1f}) 이하"
        return analyst_mean or llm_target, warning

    if signal == "sell" and llm_target > current_price * 1.02:
        warning = f"sell 신호이나 목표가({llm_target:.1f})가 현재가({current_price:.1f}) 이상"
        return analyst_mean or llm_target, warning

    # 2. 절대 범위 확인 (현재가 대비 -60% ~ +200% 초과는 이상값)
    lower = current_price * 0.40
    upper = current_price * 3.00
    if not (lower <= llm_target <= upper):
        warning = f"목표가({llm_target:.1f})가 허용 범위({lower:.1f}~{upper:.1f}) 이탈"
        return analyst_mean or llm_target, warning

    # 3. 애널리스트 컨센서스 대비 편차 확인 (60% 초과 시 블렌딩)
    if analyst_mean and analyst_mean > 0:
        deviation = abs(llm_target - analyst_mean) / analyst_mean
        if deviation > 0.60:
            blended = round((llm_target + analyst_mean) / 2, 2)
            warning = (
                f"LLM 목표가({llm_target:.1f})가 컨센서스({analyst_mean:.1f})와 "
                f"{deviation*100:.0f}% 괴리 → 블렌딩({blended:.1f})"
            )
            return blended, warning

    return llm_target, None


def _atr_stop_loss(
    current_price: float,
    atr_14: float | None,
    signal: str,
    multiplier: float = 2.0,
) -> tuple[float, str]:
    """ATR 기반 손절가 계산.

    변동성(ATR)의 2배를 현재가에서 차감 — 일반적인 추세추종 손절 기준.
    ATR 없으면 현재가 -18% 기본값 사용.
    """
    if atr_14 and atr_14 > 0 and signal == "buy":
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

    if structured:
        raw_sig = (structured.get("signal") or "").lower()
        if raw_sig in ("buy", "hold", "sell"):
            llm_signal = raw_sig
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

    # ── Step 2: 퀀트 신호와 결합 ──────────────────────────────────────
    if quant_score:
        final_signal, final_confidence = _reconcile_signals(
            llm_signal,
            quant_score.get("signal", "hold"),
            quant_score.get("confidence", 0.5),
        )
    else:
        final_signal = llm_signal or "hold"
        basis = eval_result.get("score_normalized_100")
        final_confidence = (
            min(float(basis) / 100.0, 1.0)
            if basis is not None
            else min(float(eval_result.get("total_score", 0)) / 100.0, 1.0)
        )

    # ── Step 3: 투자 기간 ────────────────────────────────────────────
    if llm_horizon:
        horizon = llm_horizon
    else:
        hm = re.search(r"(12개월|6개월|3개월|1개월)", report_text)
        horizon_map = {"12개월": "12m", "6개월": "6m", "3개월": "3m", "1개월": "1m"}
        horizon = horizon_map.get(hm.group(1), "12m") if hm else "12m"

    # ── Step 4: 목표가·손절가 검증 ───────────────────────────────────
    cp = current_price or 0.0
    validated_target = llm_target
    validated_stop = llm_stop
    if cp > 0:
        validated_target, tp_warn = _validate_target_price(
            llm_target, cp, analyst_mean, final_signal
        )
        if tp_warn:
            print(f"[signal] ⚠️  목표가 보정: {tp_warn}")

        # 손절가는 ATR 기반으로 항상 새로 계산 (LLM 값 무시)
        validated_stop, sl_msg = _atr_stop_loss(cp, atr_14, final_signal)
        print(f"[signal]    손절가: {sl_msg}")

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
        target_price=validated_target,
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
