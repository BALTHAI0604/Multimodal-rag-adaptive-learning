"""Embedding back-ends.

* SentenceTransformerEmbedder - open-source neural model (default intfloat/multilingual-e5-small,
  covers English, Hindi, Tamil, Kannada, Malayalam).  Downloaded once from Hugging Face.
* HashingEmbedder - offline *lexical* fallback (word uni/bi-grams hashed to a fixed vector).  It is
  NOT semantic: it only finds passages that share words with the question.  The API reports which
  back-end is active so the UI can say so.
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional

import numpy as np

from app.config import settings

log = logging.getLogger("embeddings")


def _l2(mat: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(mat, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (mat / n).astype(np.float32)


class HashingEmbedder:
    kind = "hashing"
    name = "hashing-lexical-v2"
    dim = 8192                      # large space => few hash collisions between unrelated words
    default_min_score = 0.07

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import HashingVectorizer
        self._vec = HashingVectorizer(n_features=self.dim, alternate_sign=False, norm=None, lowercase=True,
                                      ngram_range=(1, 2), token_pattern=r"(?u)\b\w\w+\b", stop_words="english")

    def _embed(self, texts: List[str]) -> np.ndarray:
        m = self._vec.transform(texts).toarray().astype(np.float32)
        m = np.log1p(m)                                  # sub-linear term frequency
        return _l2(m)

    def embed_passages(self, texts: List[str]) -> np.ndarray:
        return self._embed(texts)

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([text])[0]


class SentenceTransformerEmbedder:
    kind = "sentence_transformers"
    default_min_score = 0.80

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)
        self.name = model_name
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self._e5 = "e5" in model_name.lower()
        if not self._e5:
            self.default_min_score = 0.35

    def _enc(self, texts: List[str]) -> np.ndarray:
        return _l2(np.asarray(self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False),
                              dtype=np.float32))

    def embed_passages(self, texts: List[str]) -> np.ndarray:
        return self._enc([("passage: " + t) if self._e5 else t for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._enc([("query: " + text) if self._e5 else text])[0]


_embedder = None
_lock = threading.Lock()
fallback_reason: Optional[str] = None


def get_embedder():
    global _embedder, fallback_reason
    with _lock:
        if _embedder is not None:
            return _embedder
        backend = settings.EMBEDDING_BACKEND
        if backend in ("auto", "sentence_transformers"):
            try:
                _embedder = SentenceTransformerEmbedder(settings.EMBEDDING_MODEL)
                log.info("Embedding model loaded: %s (%d-d)", _embedder.name, _embedder.dim)
                return _embedder
            except Exception as exc:
                fallback_reason = f"{type(exc).__name__}: {str(exc)[:160]}"
                if backend == "sentence_transformers":
                    raise
                log.warning("Neural embedder unavailable (%s); using lexical hashing fallback.", fallback_reason)
        _embedder = HashingEmbedder()
        return _embedder


def set_embedder_for_tests(e) -> None:
    global _embedder
    _embedder = e


def min_score() -> float:
    return settings.RAG_MIN_SCORE if settings.RAG_MIN_SCORE >= 0 else get_embedder().default_min_score
