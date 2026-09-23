# TigerGraph Agentic Fraud Investigation Agent (HHGOA)

An agent that investigates the 20 HHGOA case-pack alerts against the full
IEEE-CIS-derived transaction dataset, using graph-native queries (TigerGraph
GSQL, run locally against a pandas-backed equivalent index in this
environment -- see below), GraphRAG grounding against the real fraud policy
and pattern library, and Policy v1.0's exact rules (R1-R10) to produce the
three-part answer file every case requires: the case record, a suspicious
activity report when the policy calls for one, and the next-best-action
before and after evidence-gathering.

## Status: working end to end

```bash
python data_ingest/build_index.py   # ~1 min: builds data/processed/*.pkl from the raw CSVs
python run_case_pack.py             # runs all 20 cases, writes cases/HHG-0XX.json
python -m pytest tests/ -q          # 9/9 passing, including a schema-validity smoke test on real output
```

All 20 answer files are already in [`cases/`](cases/). They pass a full
schema/consistency validation pass (every field present, every ID real,
`SAR.file` agrees with whether `FILE_REPORT` is recommended, legitimate
verdicts carry zero exposure, no duplicate actions) and split roughly
10 fraud / 10 legitimate, matching the dataset README's own hint that
"half the cases are legitimate."

## Why this doesn't hit a live TigerGraph instance (yet)

I don't have a TigerGraph account, and creating one isn't something I can
do on your behalf. `schema/schema.gsql`, `schema/loading_jobs.gsql`, and
`schema/queries/*.gsql` are written against the dataset's suggested graph
schema and are ready to load once you have a Savanna workspace or
Community Edition instance running. Until then, `agent/data_index.py`
implements the exact same five queries (`card_window`, `device_neighbors`,
`region_cluster`, `similar_prior_cases`, plus the `upsert_case` write-back)
against `data/processed/*.pkl`, built from your raw CSVs by
`data_ingest/build_index.py`. Every investigation function
(`agent/patterns.py`, `agent/investigate.py`) calls only those five methods,
so switching to a live graph is a contained change -- see
[`docs/mcp_integration.md`](docs/mcp_integration.md) for the exact mapping
and the steps to actually do it once you're on Savanna.

`case.written_to_graph` is `true` for every answer because each case genuinely
is written into a graph-shaped store (`Case`/`Evidence`/`Decision` records
that later cases in the same run retrieve as case memory -- see
`agent_case_memory` in `run_case_pack.py`); it isn't yet a live TigerGraph
write. Re-run against a real instance (`AGENT_MODE=live` in `.env`) to make
that literal.

## What you still need to do

1. **Create a TigerGraph Savanna or Community Edition instance** (I can't
   create accounts). Sign up at https://savanna.tgcloud.io or install from
   https://dl.tigergraph.com, fill in `.env` (`TG_HOST`/`TG_USERNAME`/`TG_PASSWORD`),
   then `gsql schema/schema.gsql && gsql schema/loading_jobs.gsql`, run
   `python data_ingest/stage_for_gsql.py`, and run the loading job.
2. **Set an LLM API key** if you want narrative polish
   (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY` in `.env`). The pipeline is fully
   deterministic without one -- every `fraud_probability` and evidence claim
   is computed from real graph facts, not an LLM judgment call, so the 20
   answer files don't require an API key at all. `agent/llm.py` is wired
   for when you want it (e.g. to polish the SAR narrative prose).
3. **Record the demo video** -- see [`docs/demo_script.md`](docs/demo_script.md)
   for a shot list. Point it at `cases/*.json` and, once you've loaded a
   live TigerGraph instance, GraphStudio showing the written-back `Case`
   vertices.
4. **Write the blog post and social post, and submit the form.** Draft in
   [`docs/blog_post_draft.md`](docs/blog_post_draft.md).
5. **The analyst dashboard / UI** -- built. `streamlit run ui/app.py`
   (or use `.claude/launch.json`'s `dashboard` config). Three tabs:
   *Overview* (fraud/legitimate split, pattern distribution, sortable case
   table), *Case detail* (evidence, case-memory hits, before/after
   next-best-action with what-changed, SAR narrative, raw JSON), and
   *New investigation* (runs the live pipeline against any transaction ID
   in the dataset, not just the 20 case-pack cases -- the optional
   "monitor beyond the 20 cases" capability from the dataset README).
   Visual design pass: Apple-style typography/spacing (`-apple-system`
   font stack, generous rounded cards, soft shadows) with Blinkit-style
   accent color, pill segmented tabs, custom line-icon SVG badges, and a
   pop-in/pulse-ring animation on every verdict reveal -- including a
   "order confirmed"-style success flash on the New investigation panel.
   Presentation only; nothing in `agent/`, `data_ingest/`, or `schema/`
   changed for this pass.

## Repo layout

| Path | What it is |
|---|---|
| `schema/schema.gsql` | Graph schema: Customer/Card/Transaction/DeviceProfile/EmailDomain/BillingRegion/ClosedCase (the dataset's suggested schema) + Case/Evidence/Decision (agent case memory) |
| `schema/loading_jobs.gsql`, `schema/queries/*.gsql` | Loading job + the 5 GSQL queries the agent's local index mirrors |
| `data_ingest/build_index.py` | Raw CSVs -> `data/processed/*.pkl`. Also where card_id ground-truth resolution happens -- **read this file's docstring**, it explains a real data-quality trap in the dataset (see below) |
| `data_ingest/stage_for_gsql.py` | `data/processed/*.pkl` -> flat CSVs for `schema/loading_jobs.gsql` |
| `agent/data_index.py` | The graph query layer (local now, live TigerGraph later -- same interface) |
| `agent/patterns.py` | Pattern-detection heuristics (card testing, CNP anomaly, new device, out-of-region, device ring) |
| `agent/policy_engine.py` | Fraud Policy v1.0's action list + approval routing + SAR trigger, encoded exactly |
| `agent/investigate.py` | The per-case pipeline: evidence gathering -> probability scoring -> policy rules (R1-R10) -> evidence-gathering loop -> answer assembly |
| `agent/graphrag.py` | GraphRAG: TF-IDF retrieval over the real policy/pattern docs and the 5,565 closed-case analyst narratives |
| `run_case_pack.py` | Runs all 20 cases, writes `cases/*.json` |
| `ui/app.py` | Streamlit analyst dashboard (overview, case detail, live new-investigation panel) |
| `docs/fraud_policy.md`, `docs/fraud_patterns.md` | The real Policy v1.0 and pattern-library text, verbatim from the dataset README |

## Two real data-quality findings worth knowing about

Both are documented where they're fixed, because they'd silently produce
wrong answers if missed:

1. **`card_id` isn't in the raw transaction data and can't be reconstructed
   by transaction-time ordering.** `TransactionDT` was deliberately
   perturbed by the dataset publisher, which scrambles the fine-grained
   order between two cards used close together in time -- a naive
   "first-seen order per customer" guess was right only ~50% of the time
   when a customer has 2+ cards. The fix: `closed_cases_history.csv` and
   `case_pack.csv` both give real, ground-truth `card_id` values for
   specific transaction IDs; propagating those to every other transaction
   with the exact same `(customer_id, card1..card6)` tuple resolves
   `card_id` for 77.6% of the whole dataset with zero guessing. See
   `data_ingest/build_index.py:resolve_card_ids`.
2. **Most `DeviceInfo` values are generic OS buckets, not device
   fingerprints.** `"Windows"` alone appears on 47,741 identity records --
   treating it as a shared-device signal would link nearly every desktop
   Chrome user in the dataset into one fake "ring." Only Android
   `Build/...` strings (and other genuinely rare values) are specific
   enough to use for device-ring detection; see
   `data_ingest/build_index.py:is_specific_device_info`. This one actually
   broke the first version of the pipeline (11/20 cases spuriously called
   `undocumented` ring fraud) before it was caught and fixed.

## Quickstart

```bash
python -m venv .venv
source .venv/Scripts/activate   # .venv\Scripts\activate on native Windows shells
pip install -r requirements.txt
cp .env.example .env            # fill in TG_HOST/TG_USERNAME/TG_PASSWORD once you have Savanna/CE,
                                 # and TXN_CSV/IDENTITY_CSV/CLOSED_CASES_CSV/CASE_PACK_CSV if your
                                 # raw files aren't under data/raw/

python data_ingest/build_index.py
python run_case_pack.py                    # all 20 cases
python run_case_pack.py --case HHG-011     # a single case, for debugging
python -m pytest tests/ -q

streamlit run ui/app.py                    # analyst dashboard
```
