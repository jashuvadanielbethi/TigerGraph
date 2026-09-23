#!/usr/bin/env bash
# Installs schema, loading jobs, and all GSQL queries against the configured
# TigerGraph instance. Run once after `gsql schema/schema.gsql`.
set -euo pipefail

gsql schema/schema.gsql
gsql schema/loading_jobs.gsql

for f in schema/queries/*.gsql; do
  echo "Installing $f"
  gsql "$f"
done

gsql "USE GRAPH FraudInvestigation INSTALL QUERY ALL"
