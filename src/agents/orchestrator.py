"""Orchestrator — 티커 입력 시 각 Agent를 순서대로 구동."""

from __future__ import annotations

from pathlib import Path

from .collect_agent import CollectAgent
from .analyze_agent import AnalyzeAgent
from .report_agent import ReportAgent
from .eval_agent import EvalAgent
from .signal_agent import SignalAgent


class Orchestrator:
    """
    티커 하나를 받아 5개 Agent를 순서대로 실행한다.
    각 Agent는 dict를 받아 dict를 반환하며, 다음 Agent로 전달된다.
    """

    def __init__(self) -> None:
        self.agents = [
            CollectAgent(),
            AnalyzeAgent(),
            ReportAgent(),
            EvalAgent(),
            SignalAgent(),
        ]

    def run(
        self,
        *,
        ticker: str,
        date_str: str,
        cfg: dict,
        paths: dict,
        prompts_dir: Path,
        root: Path,
        skip_llm: bool = False,
        use_judge: bool = False,
    ) -> dict:
        """
        초기 컨텍스트를 구성하고 Agent 체인을 순서대로 실행.
        최종 상태 dict를 반환한다.
        """
        state: dict = {
            "ticker": ticker,
            "date_str": date_str,
            "cfg": cfg,
            "paths": paths,
            "prompts_dir": prompts_dir,
            "root": root,
            "skip_llm": skip_llm,
            "use_judge": use_judge,
        }

        total = len(self.agents)
        for i, agent in enumerate(self.agents, start=1):
            print(f"[orchestrator] {i}/{total} → [{agent.name}] 실행", flush=True)
            state = agent.run(state)

        return state
