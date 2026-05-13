from __future__ import annotations

import math
from typing import Any

import numpy as np


class FeatureBuilder:
    def build(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        monthly = snapshot["price"]["monthly_close"]
        daily = snapshot["price"]["daily_close"]
        info = snapshot["info"]
        income = snapshot["financials"]["income_stmt"]
        annual_income = snapshot["financials"].get("annual_income_stmt") or {}

        returns = self._compute_returns(monthly)
        valuation = self._compute_valuation(info)
        health = self._compute_health(info)
        growth = self._compute_yoy_growth(income, annual_income)
        sentiment = self._simple_news_sentiment(snapshot["news"])
        vol = self._annualized_vol(daily)

        out = {
            **returns,
            "valuation": valuation,
            "health": health,
            "growth": growth,
            "sentiment": sentiment,
            "vol_annual": vol,
        }
        out["quant"] = self._compute_quant_score(snapshot, out)
        return out

    # ── 기초 지표 계산 ────────────────────────────────────────────────

    def _compute_returns(self, monthly: dict[str, float]) -> dict[str, Any]:
        prices = sorted(monthly.items(), key=lambda x: x[0])
        vals = [v for _, v in prices]
        if len(vals) < 2:
            return {}

        def ret(n: int) -> float | None:
            if len(vals) < n:
                return None
            prev = vals[-n]
            if prev == 0:
                return None
            return round((vals[-1] / prev - 1) * 100, 2)

        return {
            "return_1m": ret(2),
            "return_3m": ret(4),
            "return_6m": ret(7),
            "return_12m": ret(13),
        }

    def _annualized_vol(self, daily: dict[str, float]) -> float | None:
        if len(daily) < 5:
            return None
        sorted_items = sorted(daily.items(), key=lambda x: x[0])
        closes = [v for _, v in sorted_items]
        if any(c <= 0 for c in closes):
            return None
        log_ret = np.diff(np.log(np.array(closes, dtype=float)))
        if len(log_ret) < 2:
            return None
        return round(float(np.std(log_ret) * np.sqrt(252)), 4)

    def _compute_valuation(self, info: dict[str, Any]) -> dict[str, Any]:
        ev = info.get("enterpriseValue") or 0
        ebitda = info.get("ebitda") or 0
        ev_ebitda = None
        if ebitda not in (0, None):
            try:
                ev_ebitda = round(float(ev) / float(ebitda), 2)
            except (TypeError, ValueError):
                ev_ebitda = None
        return {
            "PER": info.get("trailingPE"),
            "Forward_PER": info.get("forwardPE"),
            "PBR": info.get("priceToBook"),
            "EV_EBITDA": ev_ebitda,
        }

    def _compute_health(self, info: dict[str, Any]) -> dict[str, Any]:
        debt = info.get("totalDebt") or 0
        cash = info.get("totalCash") or 0
        mktcap = info.get("marketCap") or 0
        fcf = info.get("freeCashflow") or 0
        try:
            net_debt = float(debt) - float(cash)
        except (TypeError, ValueError):
            net_debt = None
        fcf_yield = None
        if mktcap:
            try:
                fcf_yield = round(float(fcf) / float(mktcap) * 100, 2)
            except (TypeError, ValueError):
                fcf_yield = None
        return {
            "net_debt": net_debt,
            "FCF_yield_pct": fcf_yield,
            "profit_margin": info.get("profitMargins"),
            "gross_margin": info.get("grossMargins"),
            "operating_margin": info.get("operatingMargins"),
            "ROE": info.get("returnOnEquity"),
            "ROA": info.get("returnOnAssets"),
        }

    def _compute_yoy_growth(self, income: dict[str, Any], annual_income: dict[str, Any]) -> dict[str, Any]:
        annual_rev = annual_income.get("Total Revenue") if isinstance(annual_income, dict) else None
        if annual_rev and isinstance(annual_rev, dict):
            dates = sorted(annual_rev.keys(), reverse=True)
            values = [annual_rev[d] for d in dates]
            if len(values) >= 2:
                now, prev = values[0], values[1]
                if prev and not math.isclose(float(prev), 0):
                    yoy = round((float(now) / float(prev) - 1) * 100, 2)
                    return {"revenue_yoy_pct": yoy}

        rev = income.get("Total Revenue") if isinstance(income, dict) else None
        if rev and isinstance(rev, dict):
            dates = sorted(rev.keys(), reverse=True)
            values = [rev[d] for d in dates]
            if len(values) >= 5:
                now_q, prev_q = values[0], values[4]
                if prev_q and not math.isclose(float(prev_q), 0):
                    yoy = round((float(now_q) / float(prev_q) - 1) * 100, 2)
                    return {"revenue_yoy_pct": yoy}

        return {"revenue_yoy_pct": None}

    # ── 퀀트 점수 (100점) ─────────────────────────────────────────────
    #
    # 참조 모델:
    #   VALUATION  — Greenblatt Magic Formula (EV/EBITDA + FCF Yield)
    #   QUALITY    — Novy-Marx (2013) Gross Profitability,
    #                Fama-French 5-Factor RMW (수익성)
    #   MOMENTUM   — Jegadeesh & Titman (1993) 12-1개월 모멘텀
    #   HEALTH     — Piotroski F-Score (FCF 발생액 신호, 레버리지)
    #   GROWTH     — 매출 + EPS 복합 성장
    #
    def _compute_quant_score(self, snapshot: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:

        def _f(v: Any) -> float | None:
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        # 데이터 완성도 추적: (available_count, total_count)
        _avail: list[bool] = []

        def _scored(value: Any, max_pts: int, scoring_fn) -> int:
            """값이 있으면 채점, 없으면 만점의 절반(중립) 반환 후 완성도 기록."""
            _avail.append(value is not None)
            if value is None:
                return max_pts // 2
            return scoring_fn(value)

        info = snapshot["info"]
        breakdown: dict[str, int] = {}

        # 섹터별 보정 플래그
        sector = (info.get("sector") or "").strip()
        _is_financial = sector in ("Financial Services", "Financials")
        _is_utility   = sector == "Utilities"
        _is_realestate = sector == "Real Estate"
        _is_energy    = sector == "Energy"

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # FACTOR 1 | VALUATION — 20점
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        val = features.get("valuation") or {}
        health = features.get("health") or {}

        def _ev_score(v):
            if v <= 0:  return 0
            # Utilities·Energy: 높은 배수가 정상 — 기준 완화
            if _is_utility or _is_energy:
                if v <= 12: return 10
                if v <= 18: return 8
                if v <= 25: return 6
                if v <= 35: return 3
                return 0
            if v <= 8:  return 10
            if v <= 12: return 8
            if v <= 16: return 6
            if v <= 22: return 3
            return 0

        def _fcf_val_score(v):
            if v > 7:   return 6
            if v > 4:   return 5
            if v > 2:   return 3
            if v > 0:   return 1
            return 0

        def _fper_score(v):
            if v <= 0:  return 0
            if v <= 12: return 4
            if v <= 17: return 3
            if v <= 22: return 2
            if v <= 30: return 1
            return 0

        ev_score      = _scored(val.get("EV_EBITDA"),            10, _ev_score)
        fcf_val_score = _scored(health.get("FCF_yield_pct"),      6, _fcf_val_score)
        fper_score    = _scored(val.get("Forward_PER"),           4, _fper_score)
        breakdown["valuation"] = min(20, ev_score + fcf_val_score + fper_score)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # FACTOR 2 | QUALITY — 25점
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        def _gm_score(v):
            if v > 0.55: return 5
            if v > 0.35: return 3
            if v > 0.15: return 1
            return 0

        def _om_score(v):
            if v > 0.25: return 8
            if v > 0.15: return 6
            if v > 0.08: return 4
            if v > 0:    return 2
            return 0

        def _roe_score(v):
            if v > 0.25: return 7
            if v > 0.15: return 5
            if v > 0.08: return 3
            if v > 0:    return 1
            return 0

        def _roa_score(v):
            if v > 0.15: return 5
            if v > 0.08: return 4
            if v > 0.04: return 2
            if v > 0:    return 1
            return 0

        gm_score  = _scored(info.get("grossMargins"),            5, _gm_score)
        om_score  = _scored(health.get("operating_margin"),      8, _om_score)
        roe_score = _scored(health.get("ROE"),                   7, _roe_score)
        roa_score = _scored(info.get("returnOnAssets"),          5, _roa_score)
        breakdown["quality"] = min(25, gm_score + om_score + roe_score + roa_score)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # FACTOR 3 | MOMENTUM — 20점 (가격 데이터 기반 → None 없음)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        monthly_map = snapshot["price"]["monthly_close"]
        vals = [v for _, v in sorted(monthly_map.items(), key=lambda x: x[0])]

        def _mom(start_n: int, end_n: int) -> float | None:
            if len(vals) < start_n:
                return None
            p_end = vals[-end_n] if end_n > 0 else vals[-1]
            p_start = vals[-start_n]
            if not p_start:
                return None
            return (p_end / p_start - 1) * 100

        def _m12_score(v):
            if v > 30: return 12
            if v > 15: return 9
            if v > 5:  return 6
            if v > 0:  return 3
            return 0

        def _m6_score(v):
            if v > 15: return 8
            if v > 5:  return 6
            if v > 0:  return 3
            return 0

        m12_score = _scored(_mom(13, 2), 12, _m12_score)
        m6_score  = _scored(_mom(7, 2),   8, _m6_score)
        breakdown["momentum"] = min(20, m12_score + m6_score)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # FACTOR 4 | FINANCIAL HEALTH — 20점
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        fcf_raw  = _f(info.get("freeCashflow"))
        net_debt = _f(health.get("net_debt"))
        mktcap   = _f(info.get("marketCap"))

        # 순부채/FCF 상환연수 — net_debt·fcf 둘 다 있어야 정확히 계산 가능
        nd_fcf_val = None
        if net_debt is not None and fcf_raw is not None:
            nd_fcf_val = (net_debt, fcf_raw)

        def _debt_score(v):
            net_d, fcf = v
            if net_d <= 0:  return 8
            if fcf <= 0:    return 0
            payback = net_d / fcf
            if payback <= 2: return 6
            if payback <= 4: return 4
            if payback <= 7: return 2
            return 0

        def _de_score(v):
            if v <= 20:  return 7
            if v <= 50:  return 5
            if v <= 100: return 3
            if v <= 200: return 1
            return 0

        # FCF 품질: fcf_raw 단독으로 판단
        fcf_quality_score = 0
        if fcf_raw is not None:
            _avail.append(True)
            if fcf_raw > 0:
                fcf_quality_score = 3
                pm = _f(info.get("profitMargins"))
                if pm is not None and mktcap:
                    net_income_est = pm * mktcap
                    if net_income_est > 0 and fcf_raw >= net_income_est * 0.8:
                        fcf_quality_score = 5
        else:
            _avail.append(False)
            fcf_quality_score = 2  # 중립

        # 금융·부동산: 레버리지가 사업 구조상 높은 게 정상 → D/E·순부채 항목 중립 처리
        if _is_financial or _is_realestate:
            debt_score = 4   # 8점 만점의 중립
            de_score   = 3   # 7점 만점의 중립
            _avail.extend([False, False])  # 채점 안 한 것으로 완성도에서도 제외
        else:
            debt_score = _scored(nd_fcf_val,               8, _debt_score)
            de_score   = _scored(info.get("debtToEquity"), 7, _de_score)
        breakdown["health"] = min(20, debt_score + de_score + fcf_quality_score)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # FACTOR 5 | GROWTH — 15점
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        def _rev_score(v):
            if v > 25: return 9
            if v > 15: return 7
            if v > 7:  return 4
            if v > 0:  return 1
            return 0

        def _eg_score(v):
            if v > 0.25: return 6
            if v > 0.10: return 4
            if v > 0:    return 2
            return 0

        rev_yoy = (features.get("growth") or {}).get("revenue_yoy_pct")
        rev_score = _scored(rev_yoy,                    9, _rev_score)
        eg_score  = _scored(info.get("earningsGrowth"), 6, _eg_score)

        breakdown["growth"] = min(15, rev_score + eg_score)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 최종 집계
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        total = sum(breakdown.values())
        completeness = round(sum(_avail) / len(_avail), 3) if _avail else 1.0

        if total >= 60:
            signal = "buy"
            confidence = round(0.50 + (total - 60) / 40 * 0.45, 4)
        elif total < 40:
            signal = "sell"
            confidence = round(0.50 + (40 - total) / 40 * 0.45, 4)
        else:
            signal = "hold"
            confidence = round(0.65 - abs(total - 50) / 10 * 0.15, 4)

        return {
            "score": total,
            "signal": signal,
            "confidence": confidence,
            "breakdown": breakdown,
            "data_completeness": completeness,
        }

    def _simple_news_sentiment(self, news: list[dict[str, Any]]) -> dict[str, Any]:
        pos_kw = {"beats", "surges", "growth", "strong", "record", "upgrade", "buy"}
        neg_kw = {"miss", "decline", "loss", "cut", "downgrade", "sell", "warn", "risk"}
        pos = neg = 0
        keywords: list[str] = []
        for n in news:
            title = (n.get("title") or "").lower()
            words = set(title.replace(",", " ").split())
            hit_pos = words & pos_kw
            hit_neg = words & neg_kw
            if hit_pos:
                pos += 1
                keywords.extend(hit_pos)
            if hit_neg:
                neg += 1
                keywords.extend(hit_neg)
        neutral = max(0, len(news) - pos - neg)
        uniq_kw = list(dict.fromkeys(keywords))[:10]
        return {
            "positive": pos,
            "negative": neg,
            "neutral": neutral,
            "keywords": uniq_kw,
        }
