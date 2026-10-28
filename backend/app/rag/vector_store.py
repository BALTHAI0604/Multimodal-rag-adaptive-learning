"""FAISS vector index (exact inner-product search over L2-normalised vectors = cosine similarity).

Source of truth for embeddings is the `document_chunks` table, so the index can always be rebuilt
and can never drift from the database.  If FAISS is not importable a NumPy brute-force index with
the same interface is used.
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

log = logging.getLogger("vector_store")

try:
    import faiss  # type: ignore
    HAVE_FAISS = True
except Exception:  # pragma: no cover
    faiss = None
    HAVE_FAISS = False


class VectorIndex:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.dim: Optional[int] = None
        self.model: Optional[str] = None
        self._index = None
        self._np_ids = np.zeros(0, dtype=np.int64)
        self._np_vecs = np.zeros((0, 0), dtype=np.float32)
        self.loaded = False

    @property
    def backend(self) -> str:
        return "faiss" if HAVE_FAISS else "numpy"

    @property
    def size(self) -> int:
        with self._lock:
            if HAVE_FAISS and self._index is not None:
                return int(self._index.ntotal)
            return int(self._np_ids.shape[0])

    def reset(self, dim: int, model: str) -> None:
        with self._lock:
            self.dim, self.model, self.loaded = dim, model, True
            if HAVE_FAISS:
                self._index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
            self._np_ids = np.zeros(0, dtype=np.int64)
            self._np_vecs = np.zeros((0, dim), dtype=np.float32)

    def add(self, ids: Sequence[int], vecs: np.ndarray) -> None:
        if len(ids) == 0:
            return
        vecs = np.ascontiguousarray(vecs, dtype=np.float32)
        ids_arr = np.asarray(ids, dtype=np.int64)
        with self._lock:
            if HAVE_FAISS:
                self._index.remove_ids(ids_arr)           # idempotent re-add
                self._index.add_with_ids(vecs, ids_arr)
            else:
                keep = ~np.isin(self._np_ids, ids_arr)
                self._np_ids = np.concatenate([self._np_ids[keep], ids_arr])
                self._np_vecs = np.concatenate([self._np_vecs[keep], vecs])

    def remove(self, ids: Iterable[int]) -> None:
        ids_arr = np.asarray(list(ids), dtype=np.int64)
        if ids_arr.size == 0:
            return
        with self._lock:
            if HAVE_FAISS and self._index is not None:
                self._index.remove_ids(ids_arr)
            else:
                keep = ~np.isin(self._np_ids, ids_arr)
                self._np_ids, self._np_vecs = self._np_ids[keep], self._np_vecs[keep]

    def search(self, qvec: np.ndarray, k: int, allowed: Optional[set]) -> List[Tuple[int, float]]:
        """Top-k (chunk_id, cosine) restricted to `allowed` ids (None = unrestricted)."""
        q = np.ascontiguousarray(qvec.reshape(1, -1), dtype=np.float32)
        with self._lock:
            if self.size == 0 or (allowed is not None and not allowed):
                return []
            if HAVE_FAISS:
                if allowed is None:
                    scores, ids = self._index.search(q, k)
                else:
                    sel = faiss.IDSelectorBatch(np.asarray(sorted(allowed), dtype=np.int64))
                    params = faiss.SearchParameters()
                    params.sel = sel
                    scores, ids = self._index.search(q, k, params=params)
                return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i != -1]
            sims = self._np_vecs @ q[0]
            order = np.argsort(-sims)
            out: List[Tuple[int, float]] = []
            for idx in order:
                cid = int(self._np_ids[idx])
                if allowed is None or cid in allowed:
                    out.append((cid, float(sims[idx])))
                    if len(out) >= k:
                        break
            return out


index = VectorIndex()
