"""Runs the unchanged investigation pipeline over EVERY transaction in the
dataset and writes compact per-shard results to public/lookup/<trigger>/, so
the deployed static site can answer any transaction ID without a backend.

Resumable: shards already on disk are skipped.

Usage:
    python tools/precompute_lookup.py --trigger risk_score --workers 5
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

OUT = ROOT / "public" / "lookup"
PATTERNS = ["none", "card_testing", "card_not_present_fraud", "card_not_present_new_device",
            "out_of_region_use", "account_takeover", "undocumented"]
STATUSES = ["open", "closed_fraud", "closed_legitimate", "escalated"]
ACTIONS = ["ALLOW_TRANSACTION", "DECLINE_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS",
           "WARN_CUSTOMER", "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "BLOCK_CARD", "BLOCK_ALL_CARDS",
           "GENERATE_REPORT", "CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD"]
VERDICTS = {"fraud": "f", "legitimate": "l", "uncertain": "u"}
_idx = None
_trigger = None


def _init(trigger: str) -> None:
    global _idx, _trigger
    from agent.data_index import get_index
    import agent.investigate  # noqa: F401
    _idx, _trigger = get_index(), trigger


def _acts(items: list[dict]) -> list:
    out = []
    for a in items:
        m = re.match(r"(R\d+|Policy \w+)", a["reason"])
        out.append([ACTIONS.index(a["action"]), m.group(1) if m else ""])
    return out


def _record(txn_id: int) -> list:
    from agent.investigate import investigate
    txn = _idx.get_transaction(txn_id)
    row = pd.Series({
        "case_id": f"LIVE-{txn_id}", "flagged_txn_id": txn_id, "card_id": txn["card_id"],
        "customer_id": txn["customer_id"], "trigger_type": _trigger,
        "trigger_text": "Manually triggered from the analyst dashboard.",
        "risk_score": float(txn["risk_score"]) if _trigger == "risk_score" else None,
    })
    a = investigate(row, _idx, [])
    c = a["case"]
    er = 0
    if a["evidence_requests"]:
        r = a["evidence_requests"][0]
        er = 3 if r["type"] == "analyst_info" else (1 if "confirms" in r["assumed_response"] else 2)
    return [VERDICTS[c["verdict"]], round(c["fraud_probability"] * 100), PATTERNS.index(c["pattern"]),
            c["exposure_usd"], STATUSES.index(c["status"]), 1 if a["sar"]["file"] else 0,
            c["connected_card_ids"][:6], _acts(a["next_best_actions"]["initial"]),
            _acts(a["next_best_actions"]["final"]), er, a["tool_calls"]]


def _shard(shard: int) -> int:
    ids = [t for t in _idx.txn.index if t // 1000 == shard]
    data = {str(t): _record(int(t)) for t in ids}
    d = OUT / _trigger
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{shard}.json").write_text(json.dumps(data, separators=(",", ":")))
    return len(ids)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trigger", required=True, choices=["risk_score", "customer_report", "analyst_request"])
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()
    txn = pd.read_pickle(ROOT / "data" / "processed" / "transactions.pkl")
    shards = sorted({int(t) // 1000 for t in txn.index})
    del txn
    d = OUT / args.trigger
    todo = [s for s in shards if not (d / f"{s}.json").exists()]
    print(f"{args.trigger}: {len(todo)}/{len(shards)} shards to do", flush=True)
    start, done = time.time(), 0
    with Pool(args.workers, initializer=_init, initargs=(args.trigger,)) as pool:
        for n in pool.imap_unordered(_shard, todo):
            done += 1
            print(f"  shard {done}/{len(todo)} ({n} txns) elapsed {time.time() - start:.0f}s", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
