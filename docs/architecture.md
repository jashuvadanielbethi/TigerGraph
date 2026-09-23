# Architecture

## Pipeline

```
case_pack.csv row (HHG-001 ... HHG-020)
        |
        v
  agent/investigate.py: investigate(case_row, idx, agent_case_memory)
        |
        |-- gather evidence -------------------------------------------
        |     idx.get_transaction / idx.get_identity   (the flagged txn)
        |     idx.card_window(card_id)                 (full card history)
        |     patterns.find_card_testing_sequences       -> Policy R5 / pattern 1
        |     patterns.cnp_anomaly                        -> patterns 2/3
        |     patterns.new_device_signal                  -> pattern 3
        |     patterns.out_of_region_signal                -> pattern 4, R2/R3
        |     patterns.shared_device_ring (idx.device_neighbors) -> R6
        |     idx.similar_prior_cases                     -> case memory
        |     graphrag.policy_retriever                   -> GraphRAG text grounding
        |
        |-- score fraud_probability -------------------------------------
        |     deterministic, evidence-cited additive model (agent/investigate.py Ctx.bump)
        |     every contribution traces to a concrete graph fact -- no black-box model
        |
        |-- next-best-action, BEFORE evidence gathering -----------------
        |     _decide_initial_actions(): R5/R6 (evidence-driven, act immediately),
        |     R2 (customer_report is already a denial), R1 (verify on a weak
        |     single signal), R8 (escalate if uncertain + exposure > $500)
        |
        |-- gather more evidence if needed -------------------------------
        |     risk_score/analyst_request cases below the R5/R6 bar and
        |     outside the 0.15/0.85 stop band: simulate a customer_validation
        |     response (Policy Section 5) -- assumed CONFIRM if pre-request
        |     probability < 0.50, else assumed DENY, both directions
        |     documented in evidence_requests[].assumed_response
        |
        |-- next-best-action, AFTER evidence gathering -------------------
        |     _decide_final_actions(): R2 (denied) or R3 (confirmed)
        |     SAR reconciled against Policy 3a's trigger (_reconcile_file_report)
        |
        |-- explain + stop_reason -----------------------------------------
        |     every evidence item, decision, and the Policy Section 6
        |     stopping condition that applies, all derived from ctx state
        |     (never re-asked of an LLM, so it can't drift from what happened)
        |
        v
  cases/HHG-0XX.json  (the exact dataset Answer Format)
```

## Why the scoring is deterministic, not LLM-judged

`fraud_probability` and `pattern` are computed by a transparent, additive
heuristic (`agent/investigate.py`), not by asking an LLM to eyeball the
evidence. Every contribution -- a card-testing sequence, a new device, a
shared device fingerprint linked to confirmed fraud, a customer's direct
denial -- is a concrete number pulled from a graph query, logged as an
`Evidence` item with its source and ref, and traceable back to the exact
rule (R1-R10) it feeds into. For a fraud/compliance deliverable, this
matters more than whatever marginal accuracy an LLM might add: the numbers
are reproducible, auditable, and don't change if you rerun the pipeline.
`agent/llm.py` is wired in for optional narrative polishing (SAR prose,
case summaries) but nothing in the *decision* path depends on it.

## GraphRAG

Two retrieval paths, both TF-IDF (no external vector DB dependency),
fused into evidence the same way structured graph facts are:

1. `agent/graphrag.py:PolicyRetriever` -- paragraph-chunked retrieval over
   the real `docs/fraud_policy.md` (Policy v1.0, verbatim from the dataset
   README) and `docs/fraud_patterns.md` (the 5 known patterns). Attached to
   each case as a `source: "document"` evidence item citing the exact
   policy language, not just a bare rule number.
2. `agent/graphrag.py:NarrativeRetriever` -- retrieval over all 5,565
   closed cases' `analyst_notes` free text. Complements
   `idx.similar_prior_cases` (which matches by exact entity overlap --
   same customer/card/device/region) with a text-similarity pass over what
   analysts actually wrote, which is where an undocumented pattern (R9) is
   most likely to surface.

## Graph queries (local now, TigerGraph-ready)

`agent/data_index.py:DataIndex` implements five methods, each with a
matching `schema/queries/*.gsql` file of the same name and parameters:
`card_window`, `device_neighbors`, `region_cluster`, `similar_prior_cases`,
and the `upsert_case` write-back. No live TigerGraph instance was available
in the environment this was built in, so these run against
`data/processed/*.pkl` (built by `data_ingest/build_index.py`) instead of a
real graph connection -- see `docs/mcp_integration.md` for exactly what
changes to point them at a live Savanna/Community Edition instance via
`tigergraph-mcp` or `pyTigerGraph`. `agent/patterns.py` and
`agent/investigate.py` only ever call these five methods, so the swap is
contained to one file.

## Policy engine

`docs/fraud_policy.md` Section 1 (action list), Section 2 (approval
routing, including the `$2,500` `BLOCK_CARD` L1/L2 threshold), and Section
3a (SAR trigger) are encoded directly in `agent/policy_engine.py` --
`ACTIONS`, `AUTO_ACTIONS`, `route_for()`, `sar_required()`. Rules R1-R10
themselves live in `agent/investigate.py` because they're deeply
case-context-dependent (R5 needs the card-testing sequence, R6 needs the
ring evidence, R2/R3 need the simulated verification response), but every
rule cited in a `reason` string traces back to this file's action/route
semantics.

## Two data-quality findings that shaped the design

See the main [README](../README.md#two-real-data-quality-findings-worth-knowing-about)
for the full write-up of the `card_id` ground-truth-anchoring problem and
the generic-`DeviceInfo` false-ring problem -- both required treating a
"reasonable-looking" derivation as untrustworthy until validated against
the dataset's own ground truth (`closed_cases_history.csv`,
`case_pack.csv`), which is the same discipline the investigation logic
itself applies to every piece of evidence it cites.
