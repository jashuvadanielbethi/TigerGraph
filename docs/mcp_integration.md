# Wiring the official TigerGraph MCP server

The dataset's required components list [tigergraph-mcp](https://github.com/tigergraph/tigergraph-mcp)
for exposing graph capabilities as agent tools. This repo currently runs
the investigation locally against `data/processed/*.pkl` via
`agent/data_index.py`'s `DataIndex` class (no live TigerGraph credentials
were available in the environment this was built in -- see the main
README). Every `DataIndex` method was named and shaped to match a GSQL
query in `schema/queries/` 1:1, specifically so swapping the backend is a
contained change to one file, not a rewrite of the investigation logic.

## Setup

```bash
git clone https://github.com/tigergraph/tigergraph-mcp
cd tigergraph-mcp
# follow that repo's own README for install + running the server
```

Point your MCP client config at it, e.g.:

```json
{
  "mcpServers": {
    "tigergraph": {
      "command": "python",
      "args": ["-m", "tigergraph_mcp"],
      "env": {
        "TG_HOST": "https://your-instance.i.tgcloud.io",
        "TG_USERNAME": "tigergraph",
        "TG_PASSWORD": "changeme",
        "TG_GRAPH_NAME": "FraudInvestigation"
      }
    }
  }
}
```

## Mapping `DataIndex` methods to GSQL queries / MCP tool calls

| `DataIndex` method (agent/data_index.py) | GSQL query (schema/queries/) | MCP tool call |
|---|---|---|
| `card_window(card_id, reference_ts, hours)` | `card_window.gsql` | `run_installed_query("card_window", {...})` |
| `device_neighbors(device_profile, reference_ts, window_days, exclude_card_id)` | `device_neighbors.gsql` | `run_installed_query("device_neighbors", {...})` |
| `region_cluster(addr1, reference_ts, window_days, exclude_card_id)` | `region_cluster.gsql` | `run_installed_query("region_cluster", {...})` |
| `similar_prior_cases(customer_id, card_id, device_profile, addr1, top_k)` | `similar_prior_cases.gsql` | `run_installed_query("similar_prior_cases", {...})` |
| (case write-back, called from `agent/investigate.py`) | `upsert_case.gsql` | `run_installed_query("upsert_case", {...})` + `upsert_vertex("Evidence"/"Decision", ...)` |

## To actually switch to live TigerGraph

1. Create a Savanna workspace or install Community Edition (see main README).
2. `gsql schema/schema.gsql && gsql schema/loading_jobs.gsql`
3. `python data_ingest/stage_for_gsql.py` (reshapes the processed pickles
   into the flat CSVs `loading_jobs.gsql` expects), then run the loading job.
4. Either connect via `tigergraph-mcp` as above and give the agent an
   MCP-calling loop, or add a `pyTigerGraph`-based `TigerGraphDataIndex`
   class implementing the same five methods as `DataIndex` and swap the
   import in `agent/investigate.py`. Either way, `agent/patterns.py` and
   `agent/investigate.py` don't need to change -- they only call the five
   methods above.
