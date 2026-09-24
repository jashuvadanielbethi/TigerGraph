# TigerGraph Agentic Fraud Investigation Agent (HHGOA)

An agent that investigates the 20 HHGOA case-pack alerts against the full
IEEE-CIS-derived transaction dataset. For every case it produces the
three-part answer file the task requires: the **case** (verdict, pattern,
evidence, exposure, connected cards, similar prior cases), a **suspicious
activity report** when Fraud Policy v1.0 calls for one, and the **next best
action** with its approval route both *before* and *after* any evidence it
asks for.

The 20 answer files are in [`cases/`](cases/) (`HHG-001.json` ...
`HHG-020.json`).

## How it works

- **Fully rule-based; no LLM is used.** Fraud probability, pattern, actions,
  and SAR are computed by deterministic Python over graph-style queries, so
  results are reproducible (`tokens` is `0` in every answer file). There is
  no agent framework either: the workflow is plain code in
  `agent/investigate.py`.
- **Graph queries run locally.** `agent/data_index.py` implements the five
  queries (`card_window`, `device_neighbors`, `region_cluster`,
  `similar_prior_cases`, `upsert_case`) against `data/processed/*.pkl`. The
  matching GSQL (`schema/`) is written and ready to load into TigerGraph, but
  this repo does **not** connect to a live TigerGraph instance. See
  [`docs/mcp_integration.md`](docs/mcp_integration.md) for how to switch.
  `written_to_graph` in the answer files means the case was written to this
  local graph-shaped store, not to a live TigerGraph.
- **Policy is encoded exactly.** Actions, approval routes (`auto`/`L1`/`L2`),
  and the SAR trigger are in `agent/policy_engine.py`; rules R1-R10 are
  applied in `agent/investigate.py`.

## Run it end to end

### 1. Prerequisites

- Python 3.11+
- The four HHGOA dataset files: `transactions.csv` (~700 MB),
  `identity.csv`, `closed_cases_history.csv`, `case_pack.csv`
- About 2 GB free RAM while building the index

### 2. Install

```bash
git clone https://github.com/jashuvadanielbethi/TigerGraph.git
cd TigerGraph
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Point the code at your dataset

Copy the four CSVs into `data/raw/`, then create your env file:

```bash
cp .env.example .env
```

The defaults in `.env` (`TXN_CSV=data/raw/transactions.csv`, etc.) already
match that layout. If your CSVs live somewhere else, edit those four paths in
`.env`. Leave `AGENT_MODE=mock` and leave the TigerGraph / LLM keys blank;
they are not needed.

### 4. Build the index (once, ~1 minute)

```bash
python data_ingest/build_index.py
```

Expected output ends with:

```
77.6% of transactions sit on a ground-truth-verified card_id
OK: every case_pack.csv flagged_txn_id exists in transactions.csv.
OK: case_pack flagged_txn_id -> card_id resolution mismatches: 0 / 20
```

This writes `data/processed/*.pkl` (gitignored).

### 5. Run the 20 cases

```bash
python run_case_pack.py                  # all 20 -> cases/HHG-001.json ... HHG-020.json
python run_case_pack.py --case HHG-011   # a single case
```

Expected result: 20 files written; 10 fraud / 10 legitimate; 3 SARs filed.

### 6. Run the tests

```bash
python -m pytest tests/ -q
```

Expected: `9 passed`. The smoke test re-runs all 20 cases and checks every
answer for schema validity, real IDs, no duplicate actions, and that
`sar.file` agrees with whether `FILE_REPORT` is recommended.

### 7. (Optional) Dashboard

```bash
streamlit run ui/app.py
```

Open http://localhost:8501. Tabs: **Overview**, **Case detail** (evidence,
before/after next best action, SAR), and **New investigation** (enter any
transaction ID from the dataset to investigate it live; a red "Detected
Fraud" or green "Safe" popup shows the verdict).

## Deploy to Vercel (read-only results site)

The Streamlit dashboard cannot run on Vercel (it needs a long-running server
and the 700 MB dataset). Instead the repo ships a static, read-only site in
[`public/`](public/) that shows the 20 answer files (overview, case detail,
next best action before/after, SAR, and the Detected Fraud / Safe popup).
`vercel.json` tells Vercel to skip Python detection and just serve `public/`.

1. Import the GitHub repo in Vercel with all defaults. No framework, build
   command, or environment variables are needed.
2. Deploy.

If you change `cases/`, refresh the site's copy with
`cp cases/*.json public/cases/` and commit.

## Answer file format

Each `cases/<case_id>.json` follows the dataset's Answer Format:
`case` (status, verdict, `fraud_probability`, `pattern`, `affected_txn_ids`,
`connected_card_ids`, `exposure_usd`, `evidence`, `similar_prior_cases`,
`summary`, ...), `evidence_requests`, `next_best_actions` (`initial`,
`final`, `what_changed`), `sar`, `stop_reason`, `tool_calls`, `tokens`,
`latency_s`. Customer replies are not provided in the dataset, so any
evidence request records the assumed response in
`evidence_requests[].assumed_response`.

## Optional: load into a live TigerGraph

Not required for the steps above. If you have a Savanna workspace or
Community Edition instance:

```bash
python data_ingest/stage_for_gsql.py     # writes flat CSVs to data/staged/
gsql schema/schema.gsql
gsql schema/loading_jobs.gsql
bash schema/install_queries.sh
```

Then follow [`docs/mcp_integration.md`](docs/mcp_integration.md) to point the
agent at it. The agent itself does not talk to TigerGraph yet.

## Repo layout

| Path | What it is |
|---|---|
| `cases/` | The 20 answer files (the submission output) |
| `run_case_pack.py` | Runs all 20 cases and writes `cases/*.json` |
| `agent/investigate.py` | Per-case pipeline: evidence, probability, policy rules, evidence requests, answer |
| `agent/patterns.py` | Card testing, CNP anomaly, new device, out-of-region, device ring |
| `agent/policy_engine.py` | Fraud Policy v1.0 actions, routes, SAR trigger |
| `agent/data_index.py` | Graph query layer (local now, TigerGraph-ready) |
| `agent/graphrag.py` | TF-IDF retrieval over the policy, patterns, and closed-case notes |
| `data_ingest/build_index.py` | Raw CSVs to `data/processed/*.pkl` |
| `data_ingest/stage_for_gsql.py` | Processed data to CSVs for the GSQL loading job |
| `schema/` | GSQL schema, loading job, and the five queries |
| `ui/app.py` | Streamlit dashboard |
| `tests/` | Policy unit tests and the 20-case smoke test |
| `docs/` | Architecture, policy and pattern text, MCP notes, demo script, blog draft |

## Two data traps handled in the code

1. **`card_id` is not in the raw data**, and time-ordering can't rebuild it
   because `TransactionDT` was deliberately perturbed. It is resolved by
   propagating the real `card_id` values in `closed_cases_history.csv` and
   `case_pack.csv` to every transaction with the same card tuple. See
   `resolve_card_ids` in `data_ingest/build_index.py`.
2. **Most `DeviceInfo` values are generic** (`Windows` alone is 47,741 rows),
   so they are not treated as device fingerprints. Only specific values such
   as Android `Build/...` strings are used for device-ring detection. See
   `is_specific_device_info` in the same file.

## Limitations

- No live TigerGraph connection and no LLM (see "How it works").
- Customer/analyst replies are simulated, and the assumption is recorded per
  case.
- Region-based ring detection is deliberately not scored because it produced
  false positives in busy billing regions.
