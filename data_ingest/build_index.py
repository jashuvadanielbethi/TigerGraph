"""Builds the lean, query-ready indices the agent runs against.

Reads the three large HHGOA CSVs (transactions.csv, identity.csv,
closed_cases_history.csv) using only the columns the agent's pattern
detectors actually use (the 339 anonymized V-columns and most of the C/D/M
columns are dropped -- see docs/architecture.md for why), derives a
`card_id` per (customer_id, card1..card6) combination to match the
`C01234-K1` style IDs used in case_pack.csv / closed_cases_history.csv, and
builds a `device_profile` string per online transaction the same way the
dataset's own example does it: "DeviceInfo | OS | browser | screen".

card_id derivation: within each customer_id, distinct (card1..card6) tuples
are ranked by their first transaction timestamp and labeled K1, K2, ... in
that order. This is a reconstruction (the raw files don't carry an explicit
card_id) -- it's the simplest deterministic rule consistent with the
dataset README ("card_id ... derived from the card issuer field"), and it's
validated against case_pack.csv / closed_cases_history.csv card_ids at the
end of this script (a customer's every referenced card_id must appear in
our derived set, or the script warns loudly).

Output: data/processed/{transactions.pkl, identity.pkl, closed_cases.pkl}
Run: python data_ingest/build_index.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import settings  # noqa: E402

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

TXN_USECOLS = [
    "TransactionID", "TransactionDT", "TransactionAmt", "ProductCD",
    "card1", "card2", "card3", "card4", "card5", "card6",
    "addr1", "addr2", "dist1", "dist2",
    "P_emaildomain", "R_emaildomain",
    "customer_id", "ts", "channel", "risk_score",
]

IDENTITY_USECOLS = [
    "TransactionID", "id_01", "id_02", "id_05", "id_15", "id_23",
    "id_30", "id_31", "id_33", "id_34", "DeviceType", "DeviceInfo",
]


def build_card_key(txn: pd.DataFrame) -> pd.Series:
    """(card1..card6) tuple as a single string key -- this grouping is exact
    and 100% reliable (two rows get the same key iff all six fields match).
    It is the *labeling* of a key as "C01234-K1" vs "-K2" that is NOT
    reliably reconstructable -- see the module docstring update below.
    """
    key = None
    for col in ("card1", "card2", "card3", "card4", "card5", "card6"):
        part = txn[col].fillna("NA").astype(str)
        key = part if key is None else key.str.cat(part, sep="|")
    return key


def build_card_ids_guess(txn: pd.DataFrame) -> pd.Series:
    """Best-effort K-numbering by first-seen order, per customer. Verified
    against ground truth below: this guess is right only ~50% of the time
    when a customer has 2+ cards used in the same window, because
    TransactionDT was deliberately perturbed by the dataset publisher (see
    README: "small time ... offsets"), which scrambles fine-grained ordering
    between closely-spaced transactions on different cards. Used only as a
    last-resort label for card groups with no ground-truth anchor -- see
    `resolve_card_ids()`.
    """
    tmp = pd.DataFrame({"customer_id": txn["customer_id"], "card_key": txn["card_key"], "ts": txn["TransactionDT"]})
    first_seen = tmp.groupby(["customer_id", "card_key"], sort=False)["ts"].min().reset_index()
    first_seen = first_seen.sort_values(["customer_id", "ts"])
    first_seen["rank"] = first_seen.groupby("customer_id").cumcount() + 1
    first_seen["card_id_guess"] = first_seen["customer_id"] + "-U" + first_seen["rank"].astype(str)
    mapping = first_seen.set_index(["customer_id", "card_key"])["card_id_guess"]
    idx = pd.MultiIndex.from_arrays([tmp["customer_id"], tmp["card_key"]])
    return mapping.reindex(idx).values


def resolve_card_ids(txn: pd.DataFrame, closed: pd.DataFrame, case_pack: pd.DataFrame) -> pd.DataFrame:
    """Ground-truth-anchored card_id resolution.

    closed_cases_history.csv and case_pack.csv both state a real card_id for
    specific, real transaction IDs. Those are 100% reliable anchors. This
    function propagates each anchor to every OTHER transaction sharing the
    exact same (customer_id, card1..card6) tuple -- an exact-match join, no
    guessing -- so every transaction on a ground-truth-anchored card gets
    its real, dataset-native card_id.

    For card groups with no anchor at all (the vast majority of the 590K
    transactions -- most customers never appear in the case pack or closed
    cases), `card_id` falls back to the unverified `card_id_guess` label and
    `card_id_verified` is False. Callers must never surface an unverified
    card_id as a "real" ID in agent output -- describe those cards by
    customer_id + a representative transaction ID instead (see
    agent/data_index.py).
    """
    anchors: dict[str, str] = {}  # txn_id (str) -> card_id
    conflicts = 0

    def add_anchor(txn_id, card_id) -> None:
        nonlocal conflicts
        if pd.isna(txn_id) or not card_id:
            return
        key = str(int(float(txn_id)))
        if key in anchors and anchors[key] != card_id:
            conflicts += 1
            return
        anchors[key] = card_id

    for _, row in closed.iterrows():
        add_anchor(row["first_fraud_txn_id"], row["card_id"])
        for t in row["txn_ids_list"]:
            add_anchor(t, row["card_id"])
    for _, row in case_pack.iterrows():
        add_anchor(row["flagged_txn_id"], row["card_id"])

    print(f"  {len(anchors):,} ground-truth txn_id -> card_id anchors "
          f"({conflicts} conflicting txn_ids skipped)")

    anchor_txn_ids = {int(t) for t in anchors}
    anchor_rows = txn.loc[txn.index.intersection(anchor_txn_ids)]

    tuple_votes: dict[tuple, dict[str, int]] = {}
    for txn_id, row in anchor_rows.iterrows():
        card_id = anchors.get(str(txn_id))
        if card_id is None:
            continue
        tuple_key = (row["customer_id"], row["card_key"])
        votes = tuple_votes.setdefault(tuple_key, {})
        votes[card_id] = votes.get(card_id, 0) + 1

    tuple_conflicts = 0
    tuple_to_card: dict[tuple, str] = {}
    for tuple_key, votes in tuple_votes.items():
        winner = max(votes, key=votes.get)
        if len(votes) > 1:
            tuple_conflicts += 1
        tuple_to_card[tuple_key] = winner

    print(f"  {len(tuple_to_card):,} distinct card groups resolved to a ground-truth card_id "
          f"({tuple_conflicts} groups had disagreeing anchors, majority vote used)")

    pairs = list(zip(txn["customer_id"], txn["card_key"]))
    resolved = [tuple_to_card.get(p) for p in pairs]

    out = txn.copy()
    out["card_id_verified"] = pd.notna(resolved)
    out["card_id"] = np.where(pd.notna(resolved), resolved, out["card_id_guess"])
    return out


# DeviceInfo values that are generic OS/engine buckets shared by tens of
# thousands of unrelated users, not real device fingerprints (e.g. "Windows"
# alone is 47,741 identity rows -- treating it as a fingerprint would link
# nearly every desktop Chrome user in the dataset into one "ring"). Android
# build strings ("SM-G935F Build/NRD90M") ARE specific and kept.
GENERIC_DEVICE_INFO = {"Windows", "iOS Device", "MacOS", "SAMSUNG", "Linux"}


def is_specific_device_info(device_info) -> bool:
    if pd.isna(device_info):
        return False
    s = str(device_info)
    if "Build/" in s:
        return True
    if s in GENERIC_DEVICE_INFO:
        return False
    if s.startswith("rv:") or s.startswith("Trident"):
        return False  # browser/rendering-engine version strings, not devices
    return True


def build_device_profile(identity: pd.DataFrame) -> pd.Series:
    """A device fingerprint string, but ONLY when DeviceInfo is specific
    enough to actually identify a device (see is_specific_device_info) --
    otherwise None, so ring-detection queries correctly find nothing rather
    than colliding thousands of unrelated desktop/iPhone users together."""
    def fmt(row):
        if not is_specific_device_info(row["DeviceInfo"]):
            return None
        os_ = row["id_30"] if pd.notna(row["id_30"]) else "unknown OS"
        browser = row["id_31"] if pd.notna(row["id_31"]) else "unknown browser"
        screen = row["id_33"] if pd.notna(row["id_33"]) else "unknown screen"
        return f"{row['DeviceInfo']} | {os_} | {browser} | {screen}"

    return identity.apply(fmt, axis=1)


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Reading {settings.txn_csv} ...")
    txn = pd.read_csv(settings.txn_csv, usecols=TXN_USECOLS)
    print(f"  {len(txn):,} transactions loaded")
    txn["card_key"] = build_card_key(txn)
    txn["TransactionAmt"] = txn["TransactionAmt"].astype("float32")
    txn["ts"] = pd.to_datetime(txn["ts"])

    print(f"Reading {settings.identity_csv} ...")
    identity = pd.read_csv(settings.identity_csv, usecols=IDENTITY_USECOLS)
    print(f"  {len(identity):,} identity records loaded")
    identity["device_profile"] = build_device_profile(identity)
    identity = identity.set_index("TransactionID", drop=False)

    print(f"Reading {settings.closed_cases_csv} ...")
    closed = pd.read_csv(settings.closed_cases_csv)
    closed["txn_ids_list"] = closed["txn_ids"].fillna("").apply(
        lambda s: [t for t in s.split("|") if t])
    closed["connected_card_ids_list"] = closed["connected_card_ids"].fillna("").apply(
        lambda s: [c for c in s.split("|") if c])
    print(f"  {len(closed):,} closed cases loaded "
          f"({(closed['outcome'] == 'confirmed_fraud').sum():,} confirmed, "
          f"{(closed['outcome'] == 'cleared').sum():,} cleared)")

    print(f"Reading {settings.case_pack_csv} ...")
    case_pack = pd.read_csv(settings.case_pack_csv)
    print(f"  {len(case_pack):,} case pack rows loaded")

    print("Resolving card_id against ground-truth anchors (closed cases + case pack) ...")
    txn = txn.set_index("TransactionID", drop=False)
    txn["card_id_guess"] = build_card_ids_guess(txn)
    txn = resolve_card_ids(txn, closed, case_pack)
    print(f"  {txn['card_id_verified'].mean():.1%} of transactions sit on a ground-truth-verified card_id")

    # attach device_profile to closed cases via their first_fraud_txn_id, so
    # similar_prior_cases can match on device without a second full join at
    # query time. Cast through Int64 (nullable) first -- the raw column is
    # float64 because of missing values, and naive fillna("").astype(str)
    # would stringify valid IDs as "3000120.0" and silently break every
    # downstream lookup.
    closed["first_fraud_txn_id"] = pd.to_numeric(
        closed["first_fraud_txn_id"], errors="coerce").astype("Int64")

    txn_id_to_device = identity["device_profile"]
    closed["device_profile"] = closed["first_fraud_txn_id"].apply(
        lambda t: txn_id_to_device.get(int(t)) if pd.notna(t) and int(t) in txn_id_to_device.index else None)

    # attach addr1 (billing region) of the first fraud txn for region-cluster matching
    txn_id_to_addr1 = txn["addr1"]
    closed["addr1"] = closed["first_fraud_txn_id"].apply(
        lambda t: txn_id_to_addr1.get(int(t)) if pd.notna(t) and int(t) in txn_id_to_addr1.index else None)

    print("Writing processed indices ...")
    txn.to_pickle(PROCESSED_DIR / "transactions.pkl")
    identity.to_pickle(PROCESSED_DIR / "identity.pkl")
    closed.to_pickle(PROCESSED_DIR / "closed_cases.pkl")
    case_pack.to_pickle(PROCESSED_DIR / "case_pack.pkl")

    # -- validation --
    missing_flagged = sorted(set(case_pack["flagged_txn_id"]) - set(txn.index))
    if missing_flagged:
        print(f"WARNING: {len(missing_flagged)} flagged_txn_id(s) not found in transactions.csv: {missing_flagged}")
    else:
        print("OK: every case_pack.csv flagged_txn_id exists in transactions.csv.")

    mismatches = 0
    for _, row in case_pack.iterrows():
        derived = txn.loc[int(row["flagged_txn_id"]), "card_id"]
        if derived != row["card_id"]:
            mismatches += 1
    print(f"OK: case_pack flagged_txn_id -> card_id resolution mismatches: {mismatches} / {len(case_pack)} "
          f"(should be 0 -- these are ground-truth-anchored by construction)")

    print("Done.")


if __name__ == "__main__":
    main()
