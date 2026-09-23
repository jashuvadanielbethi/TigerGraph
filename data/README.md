# Data directory

- `raw/` -- not populated here. `.env`'s `TXN_CSV`/`IDENTITY_CSV`/
  `CLOSED_CASES_CSV`/`CASE_PACK_CSV` point directly at the files you
  provided, wherever they live on disk, so nothing needed copying in. If
  you'd rather keep a local copy, drop them here and update `.env` to the
  default `data/raw/*.csv` paths (see `.env.example`).
- `processed/` -- built by `python data_ingest/build_index.py`:
  `transactions.pkl`, `identity.pkl`, `closed_cases.pkl`, `case_pack.pkl`.
  This is what `agent/data_index.py` actually queries against. Gitignored
  (large, regenerable from the raw CSVs).
- `staged/` -- built by `python data_ingest/stage_for_gsql.py`: flat
  vertex/edge CSVs for `schema/loading_jobs.gsql`, once you have a live
  TigerGraph instance to load them into. Gitignored.
