"""EvalAgent — 규칙(M0) + LLM Judge(M2) 평가."""

from __future__ import annotations

from pathlib import Path

from eval.judge import run_llm_judge
from eval.rubric import aggregate
from eval.rules import run_all_checks
from fio.storage import write_json
from report.llm import LLMProvider

from .base import BaseAgent


class EvalAgent(BaseAgent):
    name = "eval"

    def run(self, input_data: dict) -> dict:
        report_md: str = input_data["report_md"]
        context: dict = input_data["context"]
        cfg: dict = input_data["cfg"]
        paths: dict = input_data["paths"]
        ticker: str = input_data["ticker"]
        date_str: str = input_data["date_str"]
        use_judge: bool = input_data.get("use_judge", False)
        llm_provider: LLMProvider | None = input_data.get("llm_provider")
        prompts_dir: Path = input_data["prompts_dir"]

        self.log("규칙 평가(M0) 시작")
        rule_scores = run_all_checks(report_md, context)

        judge_scores = None
        judge_flags: list[str] = []

        if use_judge:
            self.log("LLM Judge(M2) 호출 (6항목)")
            judge_llm = llm_provider or LLMProvider(cfg)
            try:
                judge_scores, judge_flags = run_llm_judge(
                    report_md, context, judge_llm, prompts_dir, cfg
                )
            except Exception as e:
                self.log(f"[경고] Judge 실패 — 규칙 점수만 사용: {e}")
                judge_flags = [f"Judge 오류: {e}"]

        agg = aggregate(rule_scores, judge_scores)
        agg["flags"] = list(agg.get("flags", [])) + judge_flags

        eval_out = {"ticker": ticker, "report_date": date_str, **agg}
        write_json(paths["artifacts_dir"] / "eval.json", eval_out)

        n_flags = len(agg.get("flags") or [])
        self.log(
            f"total={agg['total_score']} | {agg.get('rubric_mode', '')} | "
            f"{agg.get('auto_coverage', '')} | 플래그 {n_flags}건"
        )

        return {**input_data, "eval_result": agg}
