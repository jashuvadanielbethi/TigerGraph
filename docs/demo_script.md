# Demo video shot list (3-5 min)

Record with a screen recorder + your voice -- this is a timed outline so
it's quick to shoot in one take. Uses the real 20-case run already in
`cases/`.

1. **0:00-0:30 -- The problem.** One sentence on why fraud investigation is
   slow/fragmented today, and what this agent does: takes a case-pack
   alert, investigates it against the full transaction graph, decides,
   and shows its work.
2. **0:30-1:15 -- Run one case live.** `python run_case_pack.py --case HHG-011`
   in a terminal. Narrate while it runs: pulling the card's full history,
   checking for card-testing sequences, checking the device against every
   other card in the dataset, pulling similar closed cases.
3. **1:15-2:15 -- Walk it in the dashboard.** `streamlit run ui/app.py`,
   open the *Case detail* tab, select HHG-011. Point out: the evidence list
   mixing graph facts (burst count, shared device, confirmed-fraud link)
   with the customer's own report; `fraud_probability` 0.97 with the
   independent evidence types listed under "Stop reason"; the connected
   cards banner -- two OTHER customers' cards implicated via one shared
   device fingerprint, found by graph traversal, not by anyone asking
   about them directly. Then flip to *Overview* for the 10/10 fraud-vs-
   legitimate split and pattern chart across all 20 cases.
4. **2:15-3:00 -- Before/after next-best-action.** Switch to a risk-score
   case (e.g. HHG-001) and open the *Next-best-action* tab: the two-column
   before/after layout with the "what changed" banner. Point out the
   *Evidence & case memory* tab's evidence-request entry showing the
   simulated customer response that drove the change.
5. **3:00-3:40 -- Policy enforcement, not LLM judgment.** Show
   `agent/policy_engine.py`'s route table for a second. Point out that
   `FILE_REPORT` is always `L2`, `BLOCK_CARD`'s route depends on the
   $2,500 exposure threshold, and every `reason` string in the answer
   files cites a specific policy rule (R1-R10) -- this is code, not a
   model guessing.
6. **3:40-4:15 -- The graph** (once you've connected a live TigerGraph
   instance -- see README). Open GraphStudio, show the schema
   (Customer/Card/Transaction/DeviceProfile/ClosedCase/Case), and the
   written-back `Case` vertex with its `CASE_HAS_EVIDENCE` edges.
7. **4:15-4:45 -- Summary run.** Show the full-pack summary (10 fraud / 10
   legitimate / 3 SARs, `python -m pytest tests/ -q` passing), then close
   on what you'd improve next (pull from `docs/blog_post_draft.md`'s
   "what we'd improve" section).
