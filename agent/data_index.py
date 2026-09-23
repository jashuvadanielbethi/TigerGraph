"""Local, graph-equivalent query layer over the processed HHGOA indices.

This is the "TigerGraph" the agent actually queries against in this
environment (no live Savanna/Community instance is connected yet -- see
README). Every method here corresponds 1:1 to a GSQL query in
schema/queries/ (same name, same parameters, same semantics), so wiring a
real TigerGraph connection later is a matter of swapping the method body for
a `runInstalledQuery(...)` call -- see agent/tigergraph_client.py.

All lookups are exact-match joins over the ground-truth-anchored card_id
column built by data_ingest/build_index.py -- see that module's docstring
for why card_id can't be reconstructed by time-ordering alone, and how the
anchoring works.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"


@dataclass
class CardKey:
    customer_id: str
    card_key: str


class DataIndex:
    """Singleton-style loader over the processed pickles."""

    def __init__(self) -> None:
        self.txn: pd.DataFrame = pd.read_pickle(PROCESSED_DIR / "transactions.pkl")
        self.identity: pd.DataFrame = pd.read_pickle(PROCESSED_DIR / "identity.pkl")
        self.closed: pd.DataFrame = pd.read_pickle(PROCESSED_DIR / "closed_cases.pkl")

        # card_id -> (customer_id, card_key); a card_id maps to exactly one
        # tuple except in the rare case two anchors disagreed (logged at
        # ingest time and resolved by majority vote already).
        card_lookup = self.txn[["card_id", "customer_id", "card_key", "card_id_verified"]].drop_duplicates("card_id")
        self._card_id_to_key: dict[str, CardKey] = {
            row.card_id: CardKey(row.customer_id, row.card_key) for row in card_lookup.itertuples()
        }
        self._card_id_verified: dict[str, bool] = dict(zip(card_lookup["card_id"], card_lookup["card_id_verified"]))

    # -- vertex lookups -----------------------------------------------------

    def get_transaction(self, txn_id: int) -> Optional[pd.Series]:
        txn_id = int(txn_id)
        return self.txn.loc[txn_id] if txn_id in self.txn.index else None

    def get_identity(self, txn_id: int) -> Optional[pd.Series]:
        txn_id = int(txn_id)
        return self.identity.loc[txn_id] if txn_id in self.identity.index else None

    def device_profile_for(self, txn_id: int) -> Optional[str]:
        ident = self.get_identity(txn_id)
        return ident["device_profile"] if ident is not None else None

    def is_card_id_verified(self, card_id: str) -> bool:
        return self._card_id_verified.get(card_id, False)

    # -- query: card_window ---------------------------------------------

    def card_window(self, card_id: str, reference_ts: Optional[pd.Timestamp] = None,
                     hours: Optional[float] = None) -> pd.DataFrame:
        """Full transaction history for a card, optionally restricted to a
        window around `reference_ts`. Mirrors GSQL query `card_window`."""
        key = self._card_id_to_key.get(card_id)
        if key is None:
            return self.txn.iloc[0:0]
        history = self.txn[(self.txn["customer_id"] == key.customer_id)
                            & (self.txn["card_key"] == key.card_key)].sort_values("ts")
        if reference_ts is not None and hours is not None:
            lo, hi = reference_ts - timedelta(hours=hours), reference_ts + timedelta(hours=hours)
            history = history[(history["ts"] >= lo) & (history["ts"] <= hi)]
        return history

    def customer_cards(self, customer_id: str) -> list[str]:
        """Every card_id (verified or not) this customer has transacted on."""
        rows = self.txn[self.txn["customer_id"] == customer_id]
        return sorted(rows["card_id"].unique().tolist())

    # -- query: device_neighbors ------------------------------------------

    def device_neighbors(self, device_profile: str, reference_ts: Optional[pd.Timestamp] = None,
                          window_days: float = 30, exclude_card_id: Optional[str] = None) -> pd.DataFrame:
        """Every transaction sharing this exact device_profile string,
        optionally windowed around a reference time. Mirrors GSQL query
        `device_neighbors`. Returns transaction rows with card_id attached."""
        if not device_profile:
            return self.txn.iloc[0:0]
        matching_txn_ids = self.identity.index[self.identity["device_profile"] == device_profile]
        rows = self.txn.loc[self.txn.index.intersection(matching_txn_ids)]
        if reference_ts is not None:
            lo, hi = reference_ts - timedelta(days=window_days), reference_ts + timedelta(days=window_days)
            rows = rows[(rows["ts"] >= lo) & (rows["ts"] <= hi)]
        if exclude_card_id:
            rows = rows[rows["card_id"] != exclude_card_id]
        return rows.sort_values("ts")

    # -- query: region_cluster --------------------------------------------

    def region_cluster(self, addr1: float, reference_ts: Optional[pd.Timestamp] = None,
                        window_days: float = 14, exclude_card_id: Optional[str] = None) -> pd.DataFrame:
        """Every transaction billed in this addr1 region, optionally
        windowed around a reference time. Mirrors GSQL query
        `region_cluster`."""
        if pd.isna(addr1):
            return self.txn.iloc[0:0]
        rows = self.txn[self.txn["addr1"] == addr1]
        if reference_ts is not None:
            lo, hi = reference_ts - timedelta(days=window_days), reference_ts + timedelta(days=window_days)
            rows = rows[(rows["ts"] >= lo) & (rows["ts"] <= hi)]
        if exclude_card_id:
            rows = rows[rows["card_id"] != exclude_card_id]
        return rows.sort_values("ts")

    # -- query: similar_prior_cases -----------------------------------------

    def similar_prior_cases(self, customer_id: str, card_id: str, device_profile: Optional[str],
                             addr1: Optional[float], top_k: int = 5) -> pd.DataFrame:
        """Closed cases (July-Oct) that share the customer, card, device
        profile, or billing region -- the agent's case memory. Mirrors GSQL
        query `similar_prior_cases`."""
        c = self.closed
        score = pd.Series(0, index=c.index)
        score += (c["customer_id"] == customer_id).astype(int) * 3
        score += (c["card_id"] == card_id).astype(int) * 2
        score += c["connected_card_ids_list"].apply(lambda lst: card_id in lst).astype(int) * 2
        if device_profile:
            score += (c["device_profile"] == device_profile).astype(int) * 3
        if addr1 is not None and not pd.isna(addr1):
            score += (c["addr1"] == addr1).astype(int) * 1
        ranked = c.assign(_score=score)
        ranked = ranked[ranked["_score"] > 0].sort_values("_score", ascending=False)
        return ranked.head(top_k)


_index: Optional[DataIndex] = None


def get_index() -> DataIndex:
    global _index
    if _index is None:
        _index = DataIndex()
    return _index
