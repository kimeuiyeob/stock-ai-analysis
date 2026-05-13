#!/usr/bin/env python3
"""티커 입력 시 Orchestrator가 각 Agent를 순서대로 구동합니다.

수집(Collect) → 분석(Analyze) → 리포트(Report) → 평가(Eval) → 신호(Signal)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "api_guide" / ".env")

import yaml

from agents import Orchestrator
from fio.storage import write_json
from report.composer import today_str
from report.llm import write_gateway_models_log


def load_config() -> dict:
    path = ROOT / "config.yaml"
    with path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_project_root"] = str(ROOT)
    return cfg


def paths_for_date(cfg: dict, ticker: str, date_str: str) -> dict:
    base_a = Path(cfg.get("paths", {}).get("artifacts", "artifacts"))
    base_r = Path(cfg.get("paths", {}).get("reports", "reports"))
    base_t = Path(cfg.get("paths", {}).get("tracking", "tracking"))
    if not base_a.is_absolute():
        base_a = ROOT / base_a
    if not base_r.is_absolute():
        base_r = ROOT / base_r
    if not base_t.is_absolute():
        base_t = ROOT / base_t

    art_dir = base_a / ticker / date_str
    report_dir = base_r / ticker
    report_dir.mkdir(parents=True, exist_ok=True)
    art_dir.mkdir(parents=True, exist_ok=True)
    base_t.mkdir(parents=True, exist_ok=True)

    return {
        "artifacts_dir": art_dir,
        "report_md": report_dir / f"{date_str}.md",
        "tracking_csv": base_t / "prediction_log.csv",
    }


def _effective_use_judge(cfg: dict, args: argparse.Namespace) -> bool:
    if getattr(args, "no_judge", False):
        return False
    if getattr(args, "judge", False):
        return True
    return bool(cfg.get("eval", {}).get("use_llm_judge", False))


def _resolve_tickers(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    xs: list[str] = []
    if args.ticker:
        if isinstance(args.ticker, list):
            xs.extend([t.strip().upper() for t in args.ticker if str(t).strip()])
        else:
            xs.append(str(args.ticker).strip().upper())
    if getattr(args, "tickers", None):
        xs.extend([t.strip().upper() for t in args.tickers.split(",") if t.strip()])
    if getattr(args, "tickers_file", None):
        p = Path(args.tickers_file)
        if not p.is_file():
            parser.error(f"--tickers-file 을 찾을 수 없습니다: {p}")
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line:
                xs.append(line.upper())
    seen: set[str] = set()
    out: list[str] = []
    for t in xs:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def run_single(cfg: dict, ticker: str, date_str: str, args: argparse.Namespace) -> None:
    llm_cfg = cfg.get("llm", {})
    model_hint = os.environ.get("FINANCIAL_AI_MODEL") or llm_cfg.get("model", "?")
    use_judge = _effective_use_judge(cfg, args)
    skip_llm = args.skip_llm

    print(
        f"[pipeline] 시작: ticker={ticker}, date={date_str} | "
        f"{'skip-llm' if skip_llm else f'model={model_hint}'} | "
        f"{'Judge(M2)' if use_judge else 'M0'}",
        flush=True,
    )

    paths = paths_for_date(cfg, ticker, date_str)
    prompts_rel = Path(cfg.get("paths", {}).get("prompts", "prompts"))
    prompts_dir = prompts_rel if prompts_rel.is_absolute() else ROOT / prompts_rel

    orchestrator = Orchestrator()
    state = orchestrator.run(
        ticker=ticker,
        date_str=date_str,
        cfg=cfg,
        paths=paths,
        prompts_dir=prompts_dir,
        root=ROOT,
        skip_llm=skip_llm,
        use_judge=use_judge,
    )

    agg = state["eval_result"]
    tsig = state["signal"]

    print(f"\n완료: {paths['report_md']}")
    note = agg.get("grade_note", "")
    if note:
        print(f"등급 설명: {note}")
    print(
        f"eval 원점수: {agg['total_score']} | "
        f"환산≈{agg.get('score_normalized_100', agg['total_score'])}/100 | "
        f"등급: {agg['grade']}"
    )
    print(f"신호: {tsig.signal} (confidence={tsig.confidence})")


def _run_batch(cfg: dict, tickers: list[str], date_str: str, args: argparse.Namespace) -> None:
    sleep_s = float(cfg.get("ingest", {}).get("sleep_between_tickers", 1))
    batch_dir = ROOT / "artifacts" / "_batch" / date_str
    batch_dir.mkdir(parents=True, exist_ok=True)

    ok: list[str] = []
    failed: list[dict] = []

    for i, ticker in enumerate(tickers):
        if i > 0:
            print(f"[pipeline] 배치: yfinance 간격 {sleep_s}s", flush=True)
            time.sleep(sleep_s)
        print(f"[pipeline] ======== 배치 [{i + 1}/{len(tickers)}] {ticker} ========", flush=True)
        try:
            run_single(cfg, ticker, date_str, args)
            ok.append(ticker)
        except Exception as e:
            err = {"ticker": ticker, "error": str(e), "type": type(e).__name__}
            failed.append(err)
            print(f"[pipeline] [오류] {ticker}: {e}", flush=True)

    summary = {"date": date_str, "success": ok, "failed": failed, "total": len(tickers)}
    write_json(batch_dir / "batch_summary.json", summary)
    if failed:
        write_json(batch_dir / "errors.json", {"errors": failed})

    print(
        f"[pipeline] 배치 종료: 성공 {len(ok)}/{len(tickers)}, 실패 {len(failed)}",
        flush=True,
    )
    raise SystemExit(1 if failed else 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="금융AI 파이프라인 — 멀티 에이전트 구조")
    parser.add_argument("--ticker", nargs="+", default=None, help="티커 1개 또는 여러 개")
    parser.add_argument("--tickers", default=None, help="쉼표 구분 다종목: AAPL,MSFT,GOOG")
    parser.add_argument("--tickers-file", default=None, metavar="PATH")
    parser.add_argument("--date", default=None, help="날짜 폴더 (기본: 오늘 UTC)")
    parser.add_argument("--skip-llm", action="store_true", help="LLM 생략 (스텁 리포트)")
    parser.add_argument("--list-models", action="store_true", help="게이트웨이 모델 목록 조회 후 종료")
    parser.add_argument("--model-log", action="store_true", help="모델 목록 조회·logs 저장")
    parser.add_argument("--judge", action="store_true", help="이번 실행만 LLM Judge(M2) 켜기")
    parser.add_argument("--no-judge", action="store_true", help="이번 실행만 Judge 끄기")
    args = parser.parse_args()

    cfg = load_config()

    if args.list_models:
        _, err, log_text = write_gateway_models_log(ROOT, cfg)
        print(log_text)
        print(f"(저장됨: {ROOT / 'logs' / 'available_models_latest.txt'})")
        raise SystemExit(1 if err else 0)

    tickers = _resolve_tickers(args, parser)
    if not tickers:
        parser.error("--ticker, --tickers, --tickers-file 중 하나 이상 필요합니다.")

    if args.ticker and (args.tickers or args.tickers_file):
        parser.error("--ticker 와 --tickers / --tickers-file 은 동시에 사용하지 마세요.")

    date_str = args.date or today_str()

    if len(tickers) > 1:
        _run_batch(cfg, tickers, date_str, args)
        return

    run_single(cfg, tickers[0], date_str, args)


if __name__ == "__main__":
    main()
