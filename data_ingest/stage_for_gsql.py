"""Reshapes data/processed/*.pkl (built by build_index.py) into the flat
vertex/edge CSVs schema/loading_jobs.gsql expects, for loading into a real
TigerGraph Savanna/Community instance.

Only cards, transactions, and closed cases that are ground-truth-anchored
(card_id_verified == True) are staged as Card/Transaction vertices tied to a
real card_id -- unverified card groups would need a synthetic, unpublished
ID that provides no value once in the graph. This keeps the graph smaller
and every Card vertex meaningful, at the cost of not including every one of
the 590K transactions. Extend this if you want full coverage (e.g. give
unverified groups their own synthetic card_id namespace).

Run: python data_ingest/stage_for_gsql.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
STAGED_DIR = Path(__file__).resolve().parent.parent / "data" / "staged"


def main() -> None:
    STAGED_DIR.mkdir(parents=True, exist_ok=True)

    txn = pd.read_pickle(PROCESSED_DIR / "transactions.pkl")
    identity = pd.read_pickle(PROCESSED_DIR / "identity.pkl")
    closed = pd.read_pickle(PROCESSED_DIR / "closed_cases.pkl")

    verified = txn[txn["card_id_verified"]].copy()
    print(f"Staging {len(verified):,} / {len(txn):,} transactions on ground-truth-verified cards "
          f"({len(verified) / len(txn):.1%})")

    customers = verified[["customer_id"]].drop_duplicates()
    customers.to_csv(STAGED_DIR / "customers.csv", index=False)

    cards = verified[["card_id", "customer_id", "card1", "card2", "card3", "card4", "card5", "card6"]] \
        .drop_duplicates("card_id")
    cards.to_csv(STAGED_DIR / "cards.csv", index=False)

    txns_out = verified.rename(columns={
        "TransactionID": "transaction_id", "TransactionDT": "transaction_dt",
        "TransactionAmt": "transaction_amt", "ProductCD": "product_cd",
        "P_emaildomain": "p_emaildomain", "R_emaildomain": "r_emaildomain",
    })[["transaction_id", "transaction_dt", "ts", "transaction_amt", "product_cd", "channel",
        "addr1", "addr2", "dist1", "dist2", "p_emaildomain", "r_emaildomain", "risk_score"]]
    txns_out.to_csv(STAGED_DIR / "transactions.csv", index=False)

    edges_owns = cards[["customer_id", "card_id"]].drop_duplicates()
    edges_owns.to_csv(STAGED_DIR / "edges_owns.csv", index=False)

    edges_made = verified[["card_id", "TransactionID"]].rename(columns={"TransactionID": "transaction_id"})
    edges_made.to_csv(STAGED_DIR / "edges_made.csv", index=False)

    valid_identity = identity[identity["device_profile"].notna()].copy()
    devices = valid_identity[["device_profile", "DeviceInfo", "id_30", "id_31", "id_33"]].drop_duplicates("device_profile")
    devices = devices.rename(columns={
        "device_profile": "device_profile_id", "DeviceInfo": "device_info",
        "id_30": "os", "id_31": "browser", "id_33": "screen",
    })
    devices.to_csv(STAGED_DIR / "device_profiles.csv", index=False)

    edges_from_device = valid_identity[["TransactionID", "device_profile"]].rename(
        columns={"TransactionID": "transaction_id", "device_profile": "device_profile_id"})
    edges_from_device = edges_from_device[
        edges_from_device["transaction_id"].isin(set(verified["TransactionID"]))]
    edges_from_device.to_csv(STAGED_DIR / "edges_from_device.csv", index=False)

    email_domains = pd.concat([
        verified["P_emaildomain"], verified["R_emaildomain"],
    ]).dropna().drop_duplicates().rename("domain").reset_index(drop=True)
    email_domains.to_frame().to_csv(STAGED_DIR / "email_domains.csv", index=False)

    edges_email = verified[["TransactionID", "P_emaildomain"]].rename(
        columns={"TransactionID": "transaction_id", "P_emaildomain": "domain"}).dropna()
    edges_email.to_csv(STAGED_DIR / "edges_purchaser_email.csv", index=False)

    regions = verified[["addr1"]].dropna().drop_duplicates()
    regions.to_csv(STAGED_DIR / "billing_regions.csv", index=False)

    edges_billed = verified[["TransactionID", "addr1"]].dropna().rename(columns={"TransactionID": "transaction_id"})
    edges_billed.to_csv(STAGED_DIR / "edges_billed_in.csv", index=False)

    closed_out = closed.rename(columns={})[
        ["case_id", "customer_id", "card_id", "opened_at", "closed_at", "outcome", "pattern",
         "first_fraud_txn_id", "n_txns", "exposure_usd", "actions_taken", "report_filed", "analyst_notes"]
    ].copy()
    closed_out["report_filed"] = closed_out["report_filed"].fillna("No").eq("Yes")
    closed_out.to_csv(STAGED_DIR / "closed_cases.csv", index=False)

    edges_involves = closed[["case_id", "txn_ids_list"]].explode("txn_ids_list").rename(
        columns={"txn_ids_list": "transaction_id"}).dropna()
    edges_involves = edges_involves[edges_involves["transaction_id"].astype(str).isin(
        set(verified["TransactionID"].astype(str)))]
    edges_involves.to_csv(STAGED_DIR / "edges_closed_involves.csv", index=False)

    edges_on_card = closed[["case_id", "card_id"]].drop_duplicates()
    edges_on_card.to_csv(STAGED_DIR / "edges_closed_on_card.csv", index=False)

    edges_connected = closed[["case_id", "connected_card_ids_list"]].explode("connected_card_ids_list").rename(
        columns={"connected_card_ids_list": "card_id"}).dropna()
    edges_connected.to_csv(STAGED_DIR / "edges_closed_connected.csv", index=False)

    print(f"Staged CSVs written to {STAGED_DIR}")


if __name__ == "__main__":
    main()
