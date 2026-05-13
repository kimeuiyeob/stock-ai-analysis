"""BaseAgent — 모든 Agent의 공통 인터페이스."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """
    run(input_data) → output_data

    input_data / output_data 는 dict.
    각 Agent는 필요한 키만 꺼내 쓰고, 결과를 추가해서 반환한다.
    """

    name: str = "base"

    def log(self, msg: str) -> None:
        print(f"[{self.name}] {msg}", flush=True)

    @abstractmethod
    def run(self, input_data: dict) -> dict:
        """Agent 실행. 반드시 dict를 반환해야 한다."""
