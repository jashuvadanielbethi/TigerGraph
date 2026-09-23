"""GraphRAG grounding layer: unstructured text retrieval that complements
the structured graph queries in agent/data_index.py. Two corpora:

1. `PolicyRetriever` -- the fraud policy (docs/fraud_policy.md) and the five
   documented fraud patterns (docs/fraud_patterns.md), paragraph-chunked.
   Used to attach a `source: "document"` evidence item citing the exact
   policy language behind a recommendation, not just a rule number.
2. `NarrativeRetriever` -- the analyst_notes free text of all 5,565 closed
   cases. `similar_prior_cases` (data_index.py) already retrieves precedent
   by exact entity overlap (customer/card/device/region); this adds a
   text-similarity pass over what analysts actually *wrote*, which is where
   an undocumented pattern (Policy R9) is most likely to surface --
   coordinated abuse an analyst described in their own words but that
   doesn't match any of the five named patterns.

Both use a lightweight TF-IDF retriever rather than an external vector
database, so the pipeline has no extra infrastructure dependency. Swap
either for TigerGraph's native vector index without touching callers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"


@dataclass
class PolicyChunk:
    doc_id: str
    title: str
    text: str


class PolicyRetriever:
    """TF-IDF retrieval over the fraud policy + fraud pattern documents."""

    def __init__(self, doc_paths: list[Path] | None = None) -> None:
        self.chunks: list[PolicyChunk] = []
        doc_paths = doc_paths or [DOCS_DIR / "fraud_policy.md", DOCS_DIR / "fraud_patterns.md"]
        for path in doc_paths:
            if path.exists():
                self._load_doc(path)

        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None
        if self.chunks:
            self._vectorizer = TfidfVectorizer(stop_words="english")
            self._matrix = self._vectorizer.fit_transform([c.text for c in self.chunks])

    def _load_doc(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        for i, para in enumerate(p.strip() for p in text.split("\n\n")):
            if len(para) >= 40:
                self.chunks.append(PolicyChunk(doc_id=f"{path.stem}#{i}", title=path.stem, text=para))

    def retrieve(self, query: str, top_k: int = 3) -> list[PolicyChunk]:
        if not self.chunks or self._vectorizer is None:
            return []
        sims = cosine_similarity(self._vectorizer.transform([query]), self._matrix).flatten()
        ranked = sorted(zip(sims, self.chunks), key=lambda x: x[0], reverse=True)
        return [chunk for score, chunk in ranked[:top_k] if score > 0]


class NarrativeRetriever:
    """TF-IDF retrieval over closed_cases_history.csv's analyst_notes --
    lets the agent find precedent by what analysts wrote, not just by
    shared entities. Loads lazily since it depends on the processed pickle
    existing (data_ingest/build_index.py must have run first)."""

    def __init__(self) -> None:
        self.case_ids: list[str] = []
        self.texts: list[str] = []
        self._vectorizer: TfidfVectorizer | None = None
        self._matrix = None

        path = PROCESSED_DIR / "closed_cases.pkl"
        if not path.exists():
            return
        closed = pd.read_pickle(path)
        notes = closed["analyst_notes"].fillna("")
        mask = notes.str.len() > 20
        self.case_ids = closed.loc[mask, "case_id"].tolist()
        self.texts = notes.loc[mask].tolist()
        if self.texts:
            self._vectorizer = TfidfVectorizer(stop_words="english", max_features=20000)
            self._matrix = self._vectorizer.fit_transform(self.texts)

    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[str, str, float]]:
        """Returns [(case_id, narrative_text, similarity_score), ...]."""
        if self._vectorizer is None or not query.strip():
            return []
        sims = cosine_similarity(self._vectorizer.transform([query]), self._matrix).flatten()
        ranked = sorted(zip(sims, self.case_ids, self.texts), key=lambda x: x[0], reverse=True)
        return [(cid, text, float(score)) for score, cid, text in ranked[:top_k] if score > 0]


policy_retriever = PolicyRetriever()
_narrative_retriever: NarrativeRetriever | None = None


def get_narrative_retriever() -> NarrativeRetriever:
    global _narrative_retriever
    if _narrative_retriever is None:
        _narrative_retriever = NarrativeRetriever()
    return _narrative_retriever
