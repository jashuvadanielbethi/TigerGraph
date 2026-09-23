"""Pattern detection: turns graph queries (agent/data_index.py) into the raw
signals the investigator (agent/investigate.py) scores and cites as
evidence. Every function here is a deterministic, explainable heuristic over
real transaction/identity/closed-case data -- no ML model, no black box, so
every fraud_probability contribution in the final case can be traced back to
a concrete number pulled from the graph. Column semantics follow the
dataset README exactly (id_15 device New/Found, id_23 proxy rating, addr1
billing region, channel in_person/online, etc.).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import pandas as pd

from agent.data_index import DataIndex

SMALL_TXN_THRESHOLD = 5.0
LARGE_TXN_THRESHOLD = 100.0


def find_card_testing_sequences(card_hist: pd.DataFrame) -> list[dict]:
    """Three or more online authorizations under $5 within an hour, followed
    by a larger purchase within 3 hours of the last small one. Pattern 1 /
    Policy R5."""
    online = card_hist[card_hist["channel"] == "online"].sort_values("ts")
    small = online[online["TransactionAmt"] < SMALL_TXN_THRESHOLD]
    ids, times = small.index.tolist(), small["ts"].tolist()

    results = []
    i, n = 0, len(ids)
    while i < n:
        j = i
        while j + 1 < n and (times[j + 1] - times[i]) <= timedelta(hours=1):
            j += 1
        if j - i + 1 >= 3:
            last_time = times[j]
            follow = card_hist[
                (card_hist["ts"] > last_time)
                & (card_hist["ts"] <= last_time + timedelta(hours=3))
                & (card_hist["TransactionAmt"] > LARGE_TXN_THRESHOLD)
            ]
            results.append({
                "small_txn_ids": ids[i:j + 1],
                "big_txn_ids": follow.index.tolist(),
                "window_start": times[i],
                "window_end": last_time,
            })
            i = j + 1
        else:
            i += 1
    return results


def cnp_anomaly(flagged: pd.Series, card_hist: pd.DataFrame) -> Optional[dict]:
    """Card-not-present anomaly: amount/product inconsistent with this
    card's history, plus a burst count. Patterns 2/3, Policy R1-R4."""
    if flagged["channel"] != "online":
        return None
    before = card_hist[card_hist["ts"] < flagged["ts"]]
    reasons: list[str] = []
    novel_product = False
    amt_z = None

    if not before.empty:
        known_products = set(before["ProductCD"].dropna().unique())
        if flagged["ProductCD"] not in known_products:
            novel_product = True
            reasons.append(
                f"product code '{flagged['ProductCD']}' has never been used on this card before "
                f"(prior products: {sorted(known_products) if known_products else 'none'})"
            )
        amts = before["TransactionAmt"]
        if len(amts) >= 3 and amts.std() and amts.std() > 0:
            amt_z = float((flagged["TransactionAmt"] - amts.mean()) / amts.std())
            if amt_z > 3:
                reasons.append(
                    f"amount ${flagged['TransactionAmt']:.2f} is {amt_z:.1f} standard deviations "
                    f"above this card's historical average (${amts.mean():.2f})"
                )

    window = card_hist[
        (card_hist["channel"] == "online")
        & (card_hist["ts"] >= flagged["ts"] - timedelta(hours=48))
        & (card_hist["ts"] <= flagged["ts"] + timedelta(hours=48))
    ]
    return {
        "novel_product": novel_product,
        "amt_z": amt_z,
        "reasons": reasons,
        "burst_txn_ids": window.index.tolist(),
        "has_prior_history": not before.empty,
    }


def new_device_signal(flagged_identity: Optional[pd.Series]) -> Optional[dict]:
    """Pattern 3: device marked New for this account, optionally behind a
    proxy."""
    if flagged_identity is None:
        return None
    is_new = str(flagged_identity.get("id_15", "")) == "New"
    proxy = flagged_identity.get("id_23")
    hidden_proxy = isinstance(proxy, str) and "hidden" in proxy.lower()
    return {"is_new": is_new, "proxy": proxy if isinstance(proxy, str) else None,
            "hidden_proxy": hidden_proxy}


def out_of_region_signal(flagged: pd.Series, card_hist: pd.DataFrame) -> Optional[dict]:
    """Pattern 4: card-present purchase in a billing region this card has no
    prior history in. Distinguishes a likely trip (multi-day activity, no
    parallel home-region activity) from likely cloning (parallel home-region
    activity continuing at the same time). Policy R2, R3."""
    if flagged["channel"] != "in_person" or pd.isna(flagged["addr1"]):
        return None
    before = card_hist[card_hist["ts"] < flagged["ts"]]
    home_regions = before["addr1"].dropna()
    if home_regions.empty:
        return None
    home_set = set(home_regions.unique())
    if flagged["addr1"] in home_set:
        return None

    near = card_hist[(card_hist["ts"] >= flagged["ts"] - timedelta(days=2))
                      & (card_hist["ts"] <= flagged["ts"] + timedelta(days=2))]
    parallel_home_activity = near[near["addr1"].isin(home_set)]

    new_region_span = card_hist[
        (card_hist["addr1"] == flagged["addr1"])
        & (card_hist["ts"] >= flagged["ts"] - timedelta(days=3))
        & (card_hist["ts"] <= flagged["ts"] + timedelta(days=3))
    ]
    span_days = 0.0
    if len(new_region_span) > 1:
        span_days = (new_region_span["ts"].max() - new_region_span["ts"].min()).total_seconds() / 86400

    home_mode = home_regions.mode()
    return {
        "home_region": float(home_mode.iloc[0]) if not home_mode.empty else None,
        "flagged_region": float(flagged["addr1"]),
        "parallel_home_activity_txn_ids": parallel_home_activity.index.tolist(),
        "new_region_span_days": span_days,
        "new_region_txn_ids": new_region_span.index.tolist(),
    }


def shared_device_ring(idx: DataIndex, device_profile: Optional[str], card_id: str,
                        reference_ts: Optional[pd.Timestamp]) -> Optional[dict]:
    """Policy R6 (shared origin): other verified cards transacting from the
    exact same device profile within a 30-day window."""
    if not device_profile:
        return None
    neighbors = idx.device_neighbors(device_profile, reference_ts=reference_ts,
                                      window_days=30, exclude_card_id=card_id)
    if neighbors.empty:
        return None
    verified_cards = sorted({c for c in neighbors["card_id"].unique() if idx.is_card_id_verified(c)})
    return {
        "neighbor_txn_count": len(neighbors),
        "verified_connected_cards": verified_cards,
        "sample_txn_ids": neighbors.index.tolist()[:10],
        "distinct_customers": sorted(neighbors["customer_id"].unique().tolist()),
    }


def region_cluster_ring(idx: DataIndex, addr1: Optional[float], card_id: str,
                         reference_ts: Optional[pd.Timestamp]) -> Optional[dict]:
    """Policy R6 (shared origin): other verified cards newly billing in the
    same region within a tight 3-day window -- a ring signature, distinct
    from one card's own out-of-region trip."""
    if addr1 is None or pd.isna(addr1):
        return None
    cluster = idx.region_cluster(addr1, reference_ts=reference_ts, window_days=3, exclude_card_id=card_id)
    if cluster.empty:
        return None
    verified_cards = sorted({c for c in cluster["card_id"].unique() if idx.is_card_id_verified(c)})
    return {
        "cluster_txn_count": len(cluster),
        "verified_connected_cards": verified_cards,
        "sample_txn_ids": cluster.index.tolist()[:10],
        "distinct_customers": sorted(cluster["customer_id"].unique().tolist()),
    }
