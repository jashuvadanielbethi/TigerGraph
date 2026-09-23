"""Smoke tests against the real processed HHGOA data (data/processed/*.pkl).
Skips automatically if data_ingest/build_index.py hasn't been run yet --
these are not unit tests of synthetic fixtures, they're an end-to-end check
that the pipeline runs cleanly and produces schema-valid output for real
case_pack rows.
"""
from pathlib import Path

import pandas as pd
import pytest

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
pytestmark = pytest.mark.skipif(
    not (PROCESSED_DIR / "case_pack.pkl").exists(),
    reason="data/processed/*.pkl not built -- run data_ingest/build_index.py first",
)

VALID_ACTIONS = {
    "ALLOW_TRANSACTION", "DECLINE_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS",
    "WARN_CUSTOMER", "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "BLOCK_CARD", "BLOCK_ALL_CARDS",
    "GENERATE_REPORT", "CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD",
}
VALID_PATTERNS = {"card_testing", "card_not_present_fraud", "card_not_present_new_device",
                  "out_of_region_use", "account_takeover", "undocumented", "none"}


def _run_all_cases():
    from agent.data_index import get_index
    from agent.investigate import investigate

    idx = get_index()
    case_pack = pd.read_pickle(PROCESSED_DIR / "case_pack.pkl")
    memory: list[dict] = []
    return [investigate(row, idx, memory) for _, row in case_pack.iterrows()], case_pack


def test_all_20_cases_produce_schema_valid_answers():
    answers, case_pack = _run_all_cases()
    assert len(answers) == len(case_pack) == 20

    txn_ids = {str(t) for t in pd.read_pickle(PROCESSED_DIR / "transactions.pkl").index}
    card_ids = set(pd.read_pickle(PROCESSED_DIR / "transactions.pkl")["card_id"].unique())

    for a in answers:
        c = a["case"]
        assert c["verdict"] in ("fraud", "legitimate", "uncertain")
        assert c["pattern"] in VALID_PATTERNS
        assert c["status"] in ("open", "closed_fraud", "closed_legitimate", "escalated")
        assert 0.0 <= c["fraud_probability"] <= 1.0
        assert all(t in txn_ids for t in c["affected_txn_ids"])
        assert all(cc in card_ids for cc in c["connected_card_ids"])

        if c["verdict"] == "legitimate":
            assert c["affected_txn_ids"] == []
            assert c["exposure_usd"] == 0
            assert a["sar"]["file"] is False

        has_file_report = any(x["action"] == "FILE_REPORT" for x in a["next_best_actions"]["final"])
        assert has_file_report == a["sar"]["file"]

        for phase in ("initial", "final"):
            actions = a["next_best_actions"][phase]
            names = [x["action"] for x in actions]
            assert len(names) == len(set(names)), f"duplicate action in {phase}: {names}"
            for x in actions:
                assert x["action"] in VALID_ACTIONS
                assert x["route"] in ("auto", "L1", "L2")

        if not a["evidence_requests"]:
            assert a["next_best_actions"]["initial"] == a["next_best_actions"]["final"]


def test_roughly_balanced_fraud_legitimate_split():
    # Not a strict requirement, but the dataset README says "half the cases
    # are legitimate" -- a pipeline that blocks (almost) everything or
    # clears (almost) everything is a strong signal something's broken.
    answers, _ = _run_all_cases()
    verdicts = [a["case"]["verdict"] for a in answers]
    fraud_count = verdicts.count("fraud")
    assert 4 <= fraud_count <= 16
