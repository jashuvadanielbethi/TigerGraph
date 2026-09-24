"""Tiny HTTP wrapper around agent.investigate.investigate so the web page's
"New investigation" panel can investigate ANY transaction in the dataset.
Uses only the standard library plus the existing pipeline (unchanged).

Run:  python api_server.py            (listens on http://localhost:8000)
Call: GET /investigate?txn=3514030&trigger=risk_score&text=optional
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from agent.data_index import get_index  # noqa: E402
from agent.investigate import investigate  # noqa: E402

PORT = 8000
TRIGGERS = {"risk_score", "customer_report", "analyst_request"}
_idx = None


def index():
    global _idx
    if _idx is None:
        _idx = get_index()
    return _idx


def run(txn_id: str, trigger: str, text: str) -> tuple[int, dict]:
    if not txn_id.isdigit():
        return 400, {"error": "Enter a numeric transaction ID."}
    if trigger not in TRIGGERS:
        return 400, {"error": "Unknown trigger."}
    idx = index()
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


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/health":
            return self._send(200, {"ok": True})
        if url.path != "/investigate":
            return self._send(404, {"error": "Not found"})
        q = parse_qs(url.query)
        code, body = run(q.get("txn", [""])[0].strip(), q.get("trigger", ["risk_score"])[0],
                         q.get("text", ["Manually triggered from the analyst dashboard."])[0])
        self._send(code, body)

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    index()
    print(f"Investigation API ready on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
