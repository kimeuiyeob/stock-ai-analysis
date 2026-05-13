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

        def _f(v: Any) -> float:
            try:
                return float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        info = snapshot["info"]
        breakdown: dict[str, int] = {}

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # FACTOR 1 | VALUATION — 20점
        # Greenblatt: 자본구조 중립 EV/EBITDA × FCF 실질 수익률 조합
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        val = features.get("valuation") or {}
        health = features.get("health") or {}

        # EV/EBITDA (10점): PER보다 자본구조·세율 왜곡이 적음
        ev_ebitda = val.get("EV_EBITDA")
        ev_score = 0
        if ev_ebitda is not None:
            ev = _f(ev_ebitda)
            if 0 < ev <= 8:    ev_score = 10   # 딥밸류
            elif ev <= 12:     ev_score = 8
            elif ev <= 16:     ev_score = 6
            elif ev <= 22:     ev_score = 3
            # 음수(EBITDA 적자) 또는 22x 초과 → 0점

        # FCF Yield (6점): 주주 실질 현금 수익률
        fcf_yield = _f(health.get("FCF_yield_pct"))
        fcf_val_score = 0
        if fcf_yield > 7:    fcf_val_score = 6
        elif fcf_yield > 4:  fcf_val_score = 5
        elif fcf_yield > 2:  fcf_val_score = 3
        elif fcf_yield > 0:  fcf_val_score = 1

        # Forward PER (4점): 컨센서스 이익 기준 상대 저평가
        fwd_per = val.get("Forward_PER")
        fper_score = 0
        if fwd_per is not None:
            p = _f(fwd_per)
            if 0 < p <= 12:   fper_score = 4
            elif p <= 17:     fper_score = 3
            elif p <= 22:     fper_score = 2
            elif p <= 30:     fper_score = 1

        breakdown["valuation"] = min(20, ev_score + fcf_val_score + fper_score)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # FACTOR 2 | QUALITY — 25점
        # Novy-Marx: 총이익률이 가장 강력한 품질 팩터
        # Fama-French RMW: 수익성 높은 기업의 초과수익 설명력
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # 총이익률 Gross Margin (5점): 가격결정력 — Novy-Marx의 핵심
        gm = _f(info.get("grossMargins"))
        gm_score = 0
        if gm > 0.55:    gm_score = 5
        elif gm > 0.35:  gm_score = 3
        elif gm > 0.15:  gm_score = 1

        # 영업이익률 Operating Margin (8점): 핵심 사업 경쟁력
        om = _f(health.get("operating_margin"))
        om_score = 0
        if om > 0.25:    om_score = 8
        elif om > 0.15:  om_score = 6
        elif om > 0.08:  om_score = 4
        elif om > 0:     om_score = 2

        # ROE (7점): 자기자본 효율 — Fama-French RMW 팩터의 대표 지표
        roe = _f(health.get("ROE"))
        roe_score = 0
        if roe > 0.25:   roe_score = 7
        elif roe > 0.15: roe_score = 5
        elif roe > 0.08: roe_score = 3
        elif roe > 0:    roe_score = 1

        # ROA (5점): 총자산 효율 — 레버리지 효과를 제거한 ROE 보완 지표
        # 부채로 부풀린 ROE를 걸러냄
        roa = _f(info.get("returnOnAssets"))
        roa_score = 0
        if roa > 0.15:   roa_score = 5
        elif roa > 0.08: roa_score = 4
        elif roa > 0.04: roa_score = 2
        elif roa > 0:    roa_score = 1

        breakdown["quality"] = min(25, gm_score + om_score + roe_score + roa_score)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # FACTOR 3 | MOMENTUM — 20점
        # Jegadeesh & Titman (1993): 12-1개월 모멘텀이 논문 검증된 핵심 시그널
        # 최근 1개월은 단기 역전(short-term reversal) 효과로 반드시 제외
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        monthly_map = snapshot["price"]["monthly_close"]
        vals = [v for _, v in sorted(monthly_map.items(), key=lambda x: x[0])]

        def _mom(start_n: int, end_n: int) -> float | None:
            """start_n개월 전 → end_n개월 전 구간 수익률 (end_n=1이면 1개월 전)"""
            if len(vals) < start_n:
                return None
            p_end = vals[-end_n] if end_n > 0 else vals[-1]
            p_start = vals[-start_n]
            if not p_start:
                return None
            return (p_end / p_start - 1) * 100

        # 12-1개월 모멘텀 (12점): J&T 논문의 핵심 — 과거 12개월 성과에서 마지막 1개월 제외
        m12_1 = _mom(13, 2)
        m12_score = 0
        if m12_1 is not None:
            if m12_1 > 30:    m12_score = 12
            elif m12_1 > 15:  m12_score = 9
            elif m12_1 > 5:   m12_score = 6
            elif m12_1 > 0:   m12_score = 3

        # 6-1개월 모멘텀 (8점): 중기 모멘텀 확인 시그널
        m6_1 = _mom(7, 2)
        m6_score = 0
        if m6_1 is not None:
            if m6_1 > 15:    m6_score = 8
            elif m6_1 > 5:   m6_score = 6
            elif m6_1 > 0:   m6_score = 3

        breakdown["momentum"] = min(20, m12_score + m6_score)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # FACTOR 4 | FINANCIAL HEALTH — 20점
        # Piotroski F-Score: FCF vs 순이익 발생액으로 이익의 질 측정
        # Altman Z-Score: 부채 상환 능력 개념 차용
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        fcf_raw = _f(info.get("freeCashflow") or 0)
        net_debt = _f(health.get("net_debt") or 0)
        mktcap = _f(info.get("marketCap") or 0)

        # 순부채 / FCF 상환연수 (8점): 실질 부채 상환 능력
        # 순현금 보유는 최고 등급, FCF로 n년 내 상환 가능한지 기준
        debt_score = 0
        if net_debt <= 0:
            debt_score = 8          # 순현금(Net Cash) 포지션
        elif fcf_raw > 0:
            payback = net_debt / fcf_raw
            if payback <= 2:    debt_score = 6
            elif payback <= 4:  debt_score = 4
            elif payback <= 7:  debt_score = 2

        # Debt/Equity (7점): 레버리지 수준 — yfinance는 % 단위 반환
        de_raw = info.get("debtToEquity")
        de_score = 0
        if de_raw is not None:
            de = _f(de_raw)         # 예: 150.0 = D/E 1.5배
            if de <= 20:     de_score = 7   # D/E ≤ 0.2배 — 거의 무부채
            elif de <= 50:   de_score = 5   # D/E ≤ 0.5배
            elif de <= 100:  de_score = 3   # D/E ≤ 1.0배
            elif de <= 200:  de_score = 1   # D/E ≤ 2.0배

        # FCF vs 순이익 비율 (5점): Piotroski 발생액 신호
        # FCF ≥ 순이익의 80% → 이익이 실제 현금으로 뒷받침됨 (이익의 질 우수)
        pm = _f(info.get("profitMargins") or 0)
        net_income_est = pm * mktcap if mktcap else 0
        fcf_quality_score = 0
        if fcf_raw > 0:
            fcf_quality_score += 3
            if net_income_est > 0 and fcf_raw >= net_income_est * 0.8:
                fcf_quality_score += 2

        breakdown["health"] = min(20, debt_score + de_score + fcf_quality_score)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # FACTOR 5 | GROWTH — 15점
        # 매출 성장 + EPS 성장 + 애널리스트 컨센서스 상승여력
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # 매출 YoY (7점): 재무제표 기반 최우선 — ingest/yahoo.py 계산값 사용
        rev_yoy = (features.get("growth") or {}).get("revenue_yoy_pct")
        rev_score = 0
        if rev_yoy is not None:
            ry = _f(rev_yoy)
            if ry > 25:    rev_score = 7
            elif ry > 15:  rev_score = 5
            elif ry > 7:   rev_score = 3
            elif ry > 0:   rev_score = 1

        # EPS 성장률 (5점): 이익의 방향성 — yfinance earningsGrowth (YoY)
        eg = info.get("earningsGrowth")
        eg_score = 0
        if eg is not None:
            eg_val = _f(eg)
            if eg_val > 0.25:    eg_score = 5
            elif eg_val > 0.10:  eg_score = 3
            elif eg_val > 0:     eg_score = 1

        # 애널리스트 컨센서스 상승여력 (3점): 기관 포워드 뷰
        # 우선순위: yfinance analyst_price_targets > Finnhub > yfinance targetMeanPrice
        at = snapshot.get("analyst_targets") or {}
        recs = snapshot.get("analyst_recs") or {}
        current_price = _f((snapshot.get("price") or {}).get("current"))
        mean_target = at.get("mean") or recs.get("mean_target")
        upside_score = 0
        if mean_target and current_price > 0:
            upside = (_f(mean_target) - current_price) / current_price * 100
            if upside > 20:    upside_score = 3
            elif upside > 10:  upside_score = 2
            elif upside > 0:   upside_score = 1

        breakdown["growth"] = min(15, rev_score + eg_score + upside_score)

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 최종 집계 및 신호 결정
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        total = sum(breakdown.values())

        if total >= 60:
            signal, confidence = "buy", round(total / 100, 4)
        elif total < 40:
            signal, confidence = "sell", round((100 - total) / 100, 4)
        else:
            signal, confidence = "hold", 0.5

        return {"score": total, "signal": signal, "confidence": confidence, "breakdown": breakdown}

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
