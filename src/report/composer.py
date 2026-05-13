from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tiktoken
from jinja2 import Environment, FileSystemLoader, select_autoescape


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class ContextBuilder:
    TOKEN_LIMIT = 5000

    def build(self, snapshot: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
        info = snapshot["info"]
        return {
            "metadata": {
                "ticker": snapshot["ticker"],
                "company_name": info.get("longName") or snapshot["ticker"],
                "sector": info.get("sector") or "N/A",
                "industry": info.get("industry") or "N/A",
                "report_date": today_str(),
                "data_as_of": snapshot["fetched_at"],
            },
            "price_summary": {
                "current_price": snapshot["price"]["current"],
                "52w_high": snapshot["price"]["52w_high"],
                "52w_low": snapshot["price"]["52w_low"],
                "returns": {k: v for k, v in features.items() if str(k).startswith("return_")},
                "vol_annual": features.get("vol_annual"),
            },
            "valuation": features["valuation"],
            "financials": {
                "quarterly_trend": self._last_4q_summary(snapshot["financials"]["income_stmt"]),
                "growth_rates": features["growth"],
                "health": features["health"],
            },
            "news_summary": {
                "recent_headlines": [n.get("title", "") for n in snapshot["news"][:5]],
                "sentiment": features["sentiment"],
            },
            "analyst_consensus": snapshot["analyst_recs"],
            "analyst_targets": self._build_analyst_targets(snapshot),
            "sec_edgar": self._build_edgar_context(snapshot.get("edgar")),
            "macro": self._build_macro_context(snapshot.get("fred")),
            "finnhub": self._build_finnhub_context(snapshot.get("finnhub")),
            "news": self._build_news_context(snapshot.get("newsapi")),
            "alphavantage": self._build_av_context(snapshot.get("alphavantage")),
            "quant_signal": features.get("quant"),
        }

    def _build_analyst_targets(self, snapshot: dict) -> dict:
        """yfinance analyst_price_targets + upgrades_downgrades 통합."""
        at = snapshot.get("analyst_targets") or {}
        ug = snapshot.get("upgrades_downgrades") or []
        # Alpha Vantage overview의 AnalystTargetPrice를 보조 참고값으로 활용
        av_target = None
        av = snapshot.get("alphavantage") or {}
        if av and not av.get("error"):
            av_target = (av.get("overview") or {}).get("analyst_target_price")

        return {
            "available": bool(at.get("mean") or av_target),
            "mean":   at.get("mean"),
            "median": at.get("median"),
            "high":   at.get("high"),
            "low":    at.get("low"),
            "av_mean": av_target,          # Alpha Vantage 보조값
            "recent_changes": ug[:5],      # 최근 5건 등급 변경
        }

    def _build_edgar_context(self, edgar: dict | None) -> dict:
        if not edgar or edgar.get("error"):
            return {"available": False, "risk_factors": []}
        return {
            "available": True,
            "filing_date": edgar.get("latest_10k_date", ""),
            "risk_factors": edgar.get("risk_factors", []),
        }

    def _build_macro_context(self, fred: dict | None) -> dict:
        if not fred or fred.get("error") == "FRED_API_KEY 없음":
            return {"available": False}
        indicators = {}
        for key in ("fed_funds_rate", "treasury_10y", "cpi_yoy", "unemployment", "gdp_growth", "dollar_index"):
            item = fred.get(key, {})
            if item.get("value") is not None:
                indicators[key] = {
                    "label": item["label"],
                    "value": item["value"],
                    "date": item["date"],
                }
        return {
            "available": bool(indicators),
            "indicators": indicators,
            "summary": fred.get("macro_summary", ""),
        }

    def _build_finnhub_context(self, finnhub: dict | None) -> dict:
        if not finnhub:
            return {"available": False}
        eps = finnhub.get("eps_surprises", [])
        insider = finnhub.get("insider_transactions", [])
        sentiment = finnhub.get("sentiment", {})
        rec = finnhub.get("recommendation", {})
        pt = finnhub.get("price_target", {})
        return {
            "available": True,
            "eps_surprises": eps,
            "insider_transactions": insider[:5],
            "sentiment": sentiment,
            "recommendation": rec,
            "price_target": pt,
        }

    def _build_news_context(self, newsapi: dict | None) -> dict:
        if not newsapi or newsapi.get("error"):
            return {"available": False}
        return {
            "available": True,
            "articles": newsapi.get("articles", [])[:5],
            "sentiment": newsapi.get("sentiment", {}),
            "top_sources": newsapi.get("top_sources", []),
        }

    def _build_av_context(self, av: dict | None) -> dict:
        if not av or av.get("error"):
            return {"available": False}
        return {
            "available": True,
            "overview": av.get("overview", {}),
            "earnings": av.get("earnings", {}),
            "annual_income": av.get("annual_income", []),
        }

    def check_token_budget(self, context: dict[str, Any]) -> dict[str, Any]:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            return {"context_tokens": -1, "within_budget": True, "warning": None}
        tokens = len(enc.encode(json.dumps(context, ensure_ascii=False)))
        return {
            "context_tokens": tokens,
            "within_budget": tokens <= self.TOKEN_LIMIT,
            "warning": None if tokens <= self.TOKEN_LIMIT else f"컨텍스트 {tokens} tokens → 요약 필요",
        }

    def _last_4q_summary(self, income: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(income, dict):
            return []
        rev = income.get("Total Revenue") or {}
        op = income.get("Operating Income") or {}
        net = income.get("Net Income") or {}
        if not isinstance(rev, dict):
            return []
        dates = sorted(rev.keys(), reverse=True)[:4]
        rows: list[dict[str, Any]] = []
        for d in dates:
            rows.append(
                {
                    "quarter": d,
                    "revenue": rev.get(d),
                    "op_income": op.get(d) if isinstance(op, dict) else None,
                    "net_income": net.get(d) if isinstance(net, dict) else None,
                }
            )
        return rows


def render_report_prompt(
    prompts_dir: Path,
    context: dict[str, Any],
    template_name: str = "report.j2",
) -> tuple[str, str]:
    env = Environment(
        loader=FileSystemLoader(str(prompts_dir)),
        autoescape=select_autoescape(enabled_extensions=()),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    tmpl = env.get_template(template_name)
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    rendered = tmpl.render(context=context, context_json=context_json, metadata=context["metadata"])
    sep = "---SYSTEM---"
    if sep in rendered:
        system_part, user_part = rendered.split(sep, 1)
        return system_part.strip(), user_part.strip()
    return "", rendered.strip()


def compose_markdown_report(
    llm: Any,
    prompts_dir: Path,
    context: dict[str, Any],
) -> str:
    system_msg, user_msg = render_report_prompt(prompts_dir, context)
    if not system_msg:
        system_msg = (
            "당신은 CFA 자격증을 보유한 금융 애널리스트입니다. "
            "제공된 데이터만 사용하고, 숫자에는 반드시 출처 태그를 붙이세요."
        )
    return llm.generate(system_msg, user_msg)
