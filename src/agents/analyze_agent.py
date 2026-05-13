"""AnalyzeAgent — 피처 계산 및 컨텍스트 생성."""

from __future__ import annotations

from features.builder import FeatureBuilder
from fio.storage import write_json
from report.composer import ContextBuilder

from .base import BaseAgent


class AnalyzeAgent(BaseAgent):
    name = "analyze"

    def run(self, input_data: dict) -> dict:
        snapshot: dict = input_data["snapshot"]
        paths: dict = input_data["paths"]

        self.log("피처·컨텍스트 계산 시작")
        fb = FeatureBuilder()
        features = fb.build(snapshot)

        cb = ContextBuilder()
        context = cb.build(snapshot, features)

        budget = cb.check_token_budget(context)
        context["_token_check"] = budget
        tok = budget.get("context_tokens", "?")
        ok_budget = budget.get("within_budget", False)

        write_json(paths["artifacts_dir"] / "context.json", context)
        self.log(f"context.json 저장 | 추정 토큰≈{tok} (예산 내: {ok_budget})")

        return {**input_data, "context": context, "features": features, "quant_score": features.get("quant", {})}
