"""Runs the agent over every case in case_pack.csv and writes one answer
file per case to cases/<case_id>.json, per the HHGOA submission format.

Requires data/processed/*.pkl to exist -- run data_ingest/build_index.py
first.

Usage:
    python run_case_pack.py
    python run_case_pack.py --case HHG-017      # single case, for debugging
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from agent.data_index import get_index  # noqa: E402
from agent.investigate import investigate  # noqa: E402

CASES_DIR = Path(__file__).resolve().parent / "cases"
PROCESSED_DIR = Path(__file__).resolve().parent / "data" / "processed"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default=None, help="Run a single case_id, e.g. HHG-017")
    args = parser.parse_args()

    CASES_DIR.mkdir(parents=True, exist_ok=True)

    case_pack = pd.read_pickle(PROCESSED_DIR / "case_pack.pkl")
    if args.case:
        case_pack = case_pack[case_pack["case_id"] == args.case]
        if case_pack.empty:
            raise SystemExit(f"No case {args.case} in case_pack.csv")

    idx = get_index()
    agent_case_memory: list[dict] = []

    for _, row in case_pack.iterrows():
        print(f"Investigating {row['case_id']} (trigger={row['trigger_type']}, "
              f"card={row['card_id']}, flagged_txn={row['flagged_txn_id']}) ...")
        answer = investigate(row, idx, agent_case_memory)
        out_path = CASES_DIR / f"{row['case_id']}.json"
        out_path.write_text(json.dumps(answer, indent=2, default=str))
        c = answer["case"]
        print(f"  -> {out_path}  verdict={c['verdict']} pattern={c['pattern']} "
              f"probability={c['fraud_probability']} exposure=${c['exposure_usd']} "
              f"sar={answer['sar']['file']} tool_calls={answer['tool_calls']} "
              f"latency={answer['latency_s']}s")

    print(f"\nDone. {len(case_pack)} answer file(s) written to {CASES_DIR}")


if __name__ == "__main__":
    main()
