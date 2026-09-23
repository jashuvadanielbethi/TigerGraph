# Building an Agentic Fraud Investigation Agent on TigerGraph (draft)

> Draft for the HHGOA blog post requirement. Fill in the bracketed sections
> with your own screenshots/voice once you've run against a live TigerGraph
> instance -- the technical content and numbers below are real, from the
> actual 20-case run in `cases/`.

## What we built

An agent that takes each of the 20 HHGOA case-pack alerts -- a bank risk
score, a customer's fraud report, or an analyst request -- and investigates
it against the full ~590K-transaction IEEE-CIS-derived dataset: pulls the
flagged card's complete transaction history, runs graph-native pattern
queries (card-testing sequences, device-ring detection via shared device
fingerprints, out-of-region use vs. cloning), retrieves grounded policy and
pattern-library text via GraphRAG, and applies the bank's Fraud Policy v1.0
rules (R1-R10) deterministically to produce a next-best-action -- once
before any additional evidence is gathered, and again after. Every case
gets three outputs in one answer file: the internal case record, a
suspicious activity report when the policy's SAR trigger is met, and the
before/after next-best-action with its approval route.

Result on the real 20-case pack: 10 fraud, 10 legitimate (the dataset
README hints "half the cases are legitimate" -- matched), 3 SAR filings,
every answer file passing a full schema/consistency validation pass.

## Architecture

[Paste the diagram from docs/architecture.md.]

The core design decision was keeping fraud-probability scoring and rule
application out of the LLM's hands entirely. `fraud_probability` is a
transparent, additive score where every contribution -- a card-testing
sequence, a device shared with a confirmed-fraud card, a customer's direct
denial -- is a concrete number pulled from a graph query and logged as
evidence with its source. Policy R1-R10 are applied as code, not asked of
a model. That's what let us record, for all 20 cases, a next-best-action
and approval route both before and after evidence gathering with full
confidence the numbers wouldn't drift between runs.

## How TigerGraph is used

- **Schema** (`schema/schema.gsql`): follows the dataset's suggested schema
  (Customer, Card, Transaction, DeviceProfile, EmailDomain, BillingRegion,
  ClosedCase) plus a Case/Evidence/Decision layer so the agent's own
  investigations become graph-native case memory.
- **Graph-native pattern detection**: the device-ring query
  (`schema/queries/device_neighbors.gsql`) is a 2-hop
  `DeviceProfile -(FROM_DEVICE)- Transaction -(MADE)- Card` traversal --
  a few lines of GSQL for a query that would be an ugly self-join in SQL.
- **Case memory as graph traversal** (`similar_prior_cases.gsql`): closed
  cases sharing the same card, device, or billing region as the current
  alert, ranked by overlap, rather than a separate vector index.

[Add: your Savanna setup notes, GraphStudio screenshot of a written-back
`Case` vertex, load time for the dataset once `stage_for_gsql.py` + the
loading job actually ran against your instance.]

## A data-quality trap worth writing up

The raw `transactions.csv` has no `card_id` column -- it has to be
reconstructed from `(customer_id, card1..card6)`. The obvious approach
(rank each customer's distinct card by first-transaction time) is wrong
about half the time whenever a customer has 2+ cards used close together,
because the dataset publisher deliberately perturbed `TransactionDT` to
prevent exact lookups against the public Kaggle file -- which also
scrambles fine-grained transaction ordering. The fix: `closed_cases_history.csv`
and `case_pack.csv` both state real, ground-truth `card_id` values for
specific transactions; propagating those across every transaction sharing
the *exact* `(customer_id, card1..card6)` tuple resolves `card_id` for
77.6% of the entire dataset with zero guessing, and 100% of the 20 graded
cases (since every case_pack row is itself an anchor). A second, related
trap: most `DeviceInfo` values ("Windows", "iOS Device") are generic OS
buckets shared by tens of thousands of unrelated people, not device
fingerprints -- treating them as one would have turned half the case pack
into a spurious "fraud ring." Both are documented in
`data_ingest/build_index.py`.

## Agentic capabilities

- **Evidence-cited probability scoring**: every fraud_probability
  contribution traces to a graph fact, capped so no single signal type
  (e.g. a customer's own history of past incidents) can dominate a new
  alert's score.
- **Policy-gated evidence gathering**: risk_score/analyst_request cases
  outside the confident band simulate a `VERIFY_WITH_CUSTOMER` response
  (Policy Section 5) and re-decide; customer_report cases skip that step
  since the report itself is already the customer's statement.
- **Human-in-the-loop by construction**: `agent/policy_engine.py`'s
  approval-routing table is the only thing that decides whether an action
  is `auto`/`L1`/`L2` -- never the LLM, never a heuristic that could drift.
- **Explainability that can't drift**: `stop_reason` and every evidence
  claim are generated from the actual investigation state, never
  re-derived by an LLM after the fact.

## What we learned

[Fill in: what surprised you re-reading the 20 answer files, which pattern
showed up most, how the undocumented-pattern detection performed against
the benchmark.]

## What we'd improve with more time

- A real per-card "is this region ALSO new for them" check for region-based
  ring detection -- currently disabled (see architecture.md) because the
  naive version is a systematic false-positive generator.
- Replace the TF-IDF retrievers with TigerGraph's native vector support.
- A real webhook for customer step-up-auth/validation responses instead of
  the current simulated confirm/deny split.
- Extend `card_id` ground-truth resolution past the anchored 77.6% (e.g. a
  confidence-scored fallback for unanchored groups instead of leaving them
  unlabeled).
