"""SignalAgent — 리포트에서 투자 신호 추출 및 트래킹 CSV 기록."""

from __future__ import annotations

import re

from fio.storage import append_prediction_row, write_json
from trading_stub.signal import extract_signal_from_report, trading_signal_to_json

from .base import BaseAgent


def _extract_horizon_text(report_text: str) -> str:
    m = re.search(r"(12개월|6개월|3개월|1개월)", report_text)
    return m.group(1) if m else ""


def _rel_report_path(report_md_path, root) -> str:
    try:
        return str(report_md_path.relative_to(root))
    except ValueError:
        return str(report_md_path)


class SignalAgent(BaseAgent):
    name = "signal"

    def run(self, input_data: dict) -> dict:
        report_md: str = input_data["report_md"]
        eval_result: dict = input_data["eval_result"]
        snapshot: dict = input_data["snapshot"]
        paths: dict = input_data["paths"]
        ticker: str = input_data["ticker"]
        date_str: str = input_data["date_str"]
        root = input_data["root"]
        quant_score: dict = input_data.get("quant_score", {})

        self.log("투자 신호 추출 및 CSV 기록")
        current_price: float | None = snapshot.get("price", {}).get("current")
        at = snapshot.get("analyst_targets") or {}
        analyst_mean: float | None = (
            at.get("mean")
            or (snapshot.get("analyst_recs") or {}).get("mean_target")
        )
        atr_14: float | None = snapshot.get("atr_14")
        tsig = extract_signal_from_report(
            report_md,
            eval_result,
            ticker,
            _rel_report_path(paths["report_md"], root),
            date_str,
            quant_score=quant_score,
            current_price=current_price,
            analyst_mean=analyst_mean,
            atr_14=atr_14,
        )
        sig_json = trading_signal_to_json(tsig)
        write_json(paths["artifacts_dir"] / "signal.json", sig_json)

        row = {
            "date": date_str,
            "ticker": ticker,
            "price_at_report": snapshot["price"]["current"],
            "opinion": tsig.signal,
            "target_price": tsig.target_price or "",
            "stop_loss": tsig.stop_loss or "",
            "horizon": tsig.time_horizon,
            "confidence": tsig.confidence,
            "quant_score": tsig.quant_score if tsig.quant_score is not None else "",
            "rubric_score": eval_result["total_score"],
            "3m_actual_price": "",
            "12m_actual_price": "",
            "direction_hit_3m": "",
            "direction_hit_12m": "",
            "excess_return_vs_sp500": "",
            "target_hit": "",
            "pe_reported": "",
            "pe_actual": snapshot["info"].get("trailingPE", ""),
            "data_accuracy_flag": "",
        }
        append_prediction_row(paths["tracking_csv"], row, list(row.keys()))
        self.log(
            f"signal.json 저장 | {tsig.signal.upper()} (quant={tsig.quant_score}, confidence={tsig.confidence})"
        )

        return {**input_data, "signal": tsig}
