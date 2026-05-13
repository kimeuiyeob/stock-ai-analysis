"""과거 시점 퀀트 점수 재계산 및 forward return 측정."""

from __future__ import annotations

import pandas as pd
import yfinance as yf

REPORTING_LAG = pd.Timedelta(days=60)


class BacktestRunner:
    def run(self, ticker: str, years: int = 3) -> pd.DataFrame:
        yf_obj = yf.Ticker(ticker)

        end = pd.Timestamp.now().normalize()
        start = end - pd.DateOffset(years=years)
        price_start = start - pd.DateOffset(years=1)
        price_end = end + pd.DateOffset(months=14)

        price_hist = yf_obj.history(
            start=price_start.strftime("%Y-%m-%d"),
            end=price_end.strftime("%Y-%m-%d"),
            auto_adjust=True,
        )
        if price_hist.empty:
            return pd.DataFrame()

        # timezone 제거 (일관성)
        if price_hist.index.tz:
            price_hist.index = price_hist.index.tz_localize(None)

        q_fin = self._safe_get(yf_obj, "quarterly_financials")
        q_bs = self._safe_get(yf_obj, "quarterly_balance_sheet")
        q_cf = self._safe_get(yf_obj, "quarterly_cashflow")
        q_earn = self._safe_get(yf_obj, "quarterly_earnings")
        shares = (yf_obj.info or {}).get("sharesOutstanding")

        signal_dates = pd.date_range(
            start=start, end=end - pd.DateOffset(months=3), freq="QE"
        )

        rows = []
        for sig_date in signal_dates:
            row = self._compute_row(
                sig_date, price_hist, q_fin, q_bs, q_cf, q_earn, shares, ticker
            )
            if row:
                rows.append(row)

        return pd.DataFrame(rows)

    # ── 유틸 ─────────────────────────────────────────────────────────

    def _safe_get(self, obj, attr):
        try:
            v = getattr(obj, attr, None)
            return None if v is None or (hasattr(v, "empty") and v.empty) else v
        except Exception:
            return None

    def _normalize_index(self, idx):
        return idx.tz_localize(None) if getattr(idx, "tz", None) else idx

    def _price_at(self, ph: pd.DataFrame, date: pd.Timestamp) -> float | None:
        sub = ph[ph.index.normalize() <= date.normalize()]
        return float(sub["Close"].iloc[-1]) if not sub.empty else None

    def _fwd_return(self, ph: pd.DataFrame, date: pd.Timestamp, days: int) -> float | None:
        entry = self._price_at(ph, date)
        target = date + pd.Timedelta(days=days)
        sub = ph[ph.index.normalize() >= target.normalize()]
        if entry is None or sub.empty:
            return None
        return round((float(sub["Close"].iloc[0]) / entry - 1) * 100, 2)

    def _hist_return(self, ph, date, days_back) -> float | None:
        past = date - pd.Timedelta(days=days_back)
        sub = ph[ph.index.normalize() <= past.normalize()]
        if sub.empty:
            return None
        entry = float(sub["Close"].iloc[-1])
        current = self._price_at(ph, date)
        return round((current / entry - 1) * 100, 2) if entry and current else None

    # ── 행 계산 ──────────────────────────────────────────────────────

    def _compute_row(
        self, sig_date, ph, q_fin, q_bs, q_cf, q_earn, shares, ticker
    ) -> dict | None:
        cutoff = sig_date - REPORTING_LAG
        price = self._price_at(ph, sig_date)
        if price is None:
            return None

        bd: dict[str, int] = {}

        # 1. 밸류에이션 (25점)
        tpe = self._trailing_pe(sig_date, price, q_earn)
        fcfy = self._fcf_yield(cutoff, price, shares, q_cf)
        v = 0
        if tpe and tpe > 0:
            if tpe < 15: v += 20
            elif tpe < 20: v += 15
            elif tpe < 25: v += 10
            elif tpe < 35: v += 5
        if fcfy:
            if fcfy > 5: v += 5
            elif fcfy > 2: v += 3
            elif fcfy > 0: v += 1
        bd["valuation"] = min(25, v)

        # 2. 모멘텀 (20점)
        r1m = self._hist_return(ph, sig_date, 21)
        r3m = self._hist_return(ph, sig_date, 63)
        r12m = self._hist_return(ph, sig_date, 252)
        m = 0
        if r1m and r1m > 3: m += 5
        elif r1m and r1m > 0: m += 3
        if r3m and r3m > 10: m += 8
        elif r3m and r3m > 0: m += 5
        if r12m and r12m > 20: m += 7
        elif r12m and r12m > 0: m += 5
        bd["momentum"] = min(20, m)

        # 3. 성장성 (20점)
        rev_yoy = self._revenue_growth(cutoff, q_fin)
        g = 0
        if rev_yoy is not None:
            if rev_yoy > 20: g = 20
            elif rev_yoy > 10: g = 15
            elif rev_yoy > 5: g = 10
            elif rev_yoy > 0: g = 5
        bd["growth"] = g

        # 4. 건전성 (20점)
        margins = self._margins(cutoff, q_fin, q_bs)
        pm = margins.get("profit_margin") or 0
        om = margins.get("operating_margin") or 0
        roe = margins.get("roe") or 0
        h = 0
        if pm > 0.20: h += 7
        elif pm > 0.10: h += 5
        elif pm > 0.05: h += 3
        elif pm > 0: h += 1
        if om > 0.25: h += 7
        elif om > 0.15: h += 5
        elif om > 0.05: h += 3
        elif om > 0: h += 1
        if roe > 0.20: h += 6
        elif roe > 0.15: h += 4
        elif roe > 0.10: h += 2
        elif roe > 0: h += 1
        bd["health"] = min(20, h)

        # 5. 애널리스트 — 역사적 데이터 없음, 중립 7점 고정
        bd["analyst"] = 7

        total = sum(bd.values())

        return {
            "ticker": ticker,
            "date": sig_date.strftime("%Y-%m-%d"),
            "score": total,
            "bd_valuation": bd["valuation"],
            "bd_momentum": bd["momentum"],
            "bd_growth": bd["growth"],
            "bd_health": bd["health"],
            "trailing_pe": tpe,
            "fcf_yield": fcfy,
            "revenue_yoy_pct": rev_yoy,
            "forward_return_3m": self._fwd_return(ph, sig_date, 63),
            "forward_return_6m": self._fwd_return(ph, sig_date, 126),
            "forward_return_12m": self._fwd_return(ph, sig_date, 252),
        }

    # ── 팩터별 계산 ──────────────────────────────────────────────────

    def _trailing_pe(self, date, price, q_earn) -> float | None:
        if q_earn is None:
            return None
        cutoff = date - REPORTING_LAG
        try:
            col = next((c for c in ["Reported EPS", "EPS"] if c in q_earn.columns), None)
            if col is None:
                return None
            idx = self._normalize_index(q_earn.index)
            avail = q_earn[idx <= cutoff]
            if len(avail) < 4:
                return None
            eps = avail[col].iloc[-4:].sum()
            return round(price / eps, 2) if eps > 0 else None
        except Exception:
            return None

    def _fcf_yield(self, cutoff, price, shares, q_cf) -> float | None:
        if q_cf is None or not shares:
            return None
        try:
            key = next((k for k in ["Free Cash Flow", "FreeCashFlow"] if k in q_cf.index), None)
            if key is None:
                return None
            row = q_cf.loc[key]
            col_idx = self._normalize_index(q_cf.columns)
            dates = sorted([d for d in col_idx if d <= cutoff], reverse=True)[:4]
            if len(dates) < 2:
                return None
            ttm = sum(row.iloc[list(col_idx).index(d)] for d in dates)
            mktcap = price * shares
            return round(ttm / mktcap * 100, 2) if mktcap > 0 else None
        except Exception:
            return None

    def _revenue_growth(self, cutoff, q_fin) -> float | None:
        if q_fin is None:
            return None
        try:
            key = next((k for k in ["Total Revenue", "Revenue"] if k in q_fin.index), None)
            if key is None:
                return None
            row = q_fin.loc[key]
            col_idx = self._normalize_index(q_fin.columns)
            dates = sorted([d for d in col_idx if d <= cutoff], reverse=True)
            if len(dates) < 8:
                return None
            vals = [row.iloc[list(col_idx).index(d)] for d in dates]
            ttm_now, ttm_prev = sum(vals[:4]), sum(vals[4:8])
            return round((ttm_now / ttm_prev - 1) * 100, 2) if ttm_prev > 0 else None
        except Exception:
            return None

    def _margins(self, cutoff, q_fin, q_bs) -> dict:
        res: dict = {}
        if q_fin is None:
            return res
        try:
            col_idx = self._normalize_index(q_fin.columns)
            dates = sorted([d for d in col_idx if d <= cutoff], reverse=True)[:4]
            if not dates:
                return res

            def ttm(keys):
                for k in keys:
                    if k in q_fin.index:
                        row = q_fin.loc[k]
                        return sum(row.iloc[list(col_idx).index(d)] for d in dates)
                return None

            rev = ttm(["Total Revenue", "Revenue"])
            ni = ttm(["Net Income", "Net Income Common Stockholders"])
            op = ttm(["Operating Income", "EBIT"])

            if rev and rev > 0:
                if ni is not None: res["profit_margin"] = ni / rev
                if op is not None: res["operating_margin"] = op / rev

            if q_bs is not None and ni is not None:
                bs_idx = self._normalize_index(q_bs.columns)
                bs_dates = sorted([d for d in bs_idx if d <= cutoff], reverse=True)[:1]
                for k in ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"]:
                    if k in q_bs.index and bs_dates:
                        eq = q_bs.loc[k].iloc[list(bs_idx).index(bs_dates[0])]
                        if eq and eq > 0:
                            res["roe"] = ni / eq
                        break
        except Exception:
            pass
        return res
