"""Vercel serverless function: runs the unchanged investigation pipeline on
ANY transaction ID from the dataset.

GET /api/investigate?txn=3514030&trigger=risk_score&text=optional

The compressed lean dataset (data/deploy/*.pkl.gz, built from
transactions.csv / identity.csv / closed_cases_history.csv by
data_ingest/build_index.py) is unpacked to /tmp on cold start, and the
pipeline is pointed at it. Nothing in agent/ is modified.
"""
from __future__ import annotations

import gzip
import json
import pickle
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import agent.data_index as data_index  # noqa: E402
from agent.investigate import investigate  # noqa: E402

TRIGGERS = {"risk_score", "customer_report", "analyst_request"}
_idx = None


class _PandasShim:
    """pandas, except read_pickle() streams the gzipped dataset straight into
    memory (Vercel's /tmp is too small to unpack it). The pipeline's own code
    is untouched; only how data_index.py opens its three files changes."""

    def __getattr__(self, name):
        return getattr(pd, name)

    def read_pickle(self, path, *args, **kwargs):
        with gzip.open(ROOT / "data" / "deploy" / f"{Path(path).stem}.pkl.gz", "rb") as f:
            return pickle.load(f)


def _index():
    global _idx
    if _idx is None:
        data_index.pd = _PandasShim()
        _idx = data_index.get_index()
    return _idx


def run(txn_id: str, trigger: str, text: str):
    if not txn_id.isdigit():
        return 400, {"error": "Enter a numeric transaction ID."}
    if trigger not in TRIGGERS:
        return 400, {"error": "Unknown trigger."}
    idx = _index()
    txn = idx.get_transaction(int(txn_id))
    if txn is None:
        return 404, {"error": f"Transaction {txn_id} not found in the dataset."}
    row = pd.Series({
        "case_id": f"LIVE-{txn_id}", "flagged_txn_id": int(txn_id),
        "card_id": txn["card_id"], "customer_id": txn["customer_id"],
        "trigger_type": trigger, "trigger_text": text,
        "risk_score": float(txn["risk_score"]) if trigger == "risk_score" else None,
    })
    return 200, investigate(row, idx, [])


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        try:
            code, body = run(q.get("txn", [""])[0].strip(), q.get("trigger", ["risk_score"])[0],
                             q.get("text", ["Manually triggered from the analyst dashboard."])[0])
        except Exception as exc:  # surfaced to the page instead of a blank 500
            code, body = 500, {"error": f"Investigation failed: {exc}"}
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "s-maxage=3600")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
