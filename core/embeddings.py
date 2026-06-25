"""
Local embeddings via fastembed (ONNX, CPU-friendly, no server).

  dense  -> BAAI/bge-small-en-v1.5  (384-dim, cosine)
  sparse -> Qdrant/bm25             (lexical, for hybrid search)

Models are loaded lazily on first use and cached as module singletons so
Streamlit reruns don't reload them. First call downloads the model
(~130 MB for bge-small) into the fastembed cache dir.
"""
from __future__ import annotations

from typing import Optional

from core.config import get_settings

_settings = get_settings()

_dense = None
_sparse = None


def _get_dense():
    global _dense
    if _dense is None:
        from fastembed import TextEmbedding
        _dense = TextEmbedding(
            model_name=_settings.embedding_model,
            threads=_settings.embed_threads,
        )
    return _dense


def _get_sparse():
    global _sparse
    if _sparse is None:
        from fastembed import SparseTextEmbedding
        _sparse = SparseTextEmbedding(
            model_name=_settings.sparse_model,
            threads=_settings.embed_threads,
        )
    return _sparse


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Dense embeddings for a batch of passages."""
    return [v.tolist() for v in _get_dense().embed(texts)]


def embed_query(text: str) -> list[float]:
    """Dense embedding for a single query (uses the query-optimized path)."""
    model = _get_dense()
    # bge models benefit from the query_embed path when available.
    try:
        return next(iter(model.query_embed(text))).tolist()
    except Exception:
        return next(iter(model.embed([text]))).tolist()


def embed_sparse_docs(texts: list[str]) -> list[dict]:
    out = []
    for s in _get_sparse().embed(texts):
        out.append({"indices": s.indices.tolist(), "values": s.values.tolist()})
    return out


def embed_sparse_query(text: str) -> dict:
    model = _get_sparse()
    try:
        s = next(iter(model.query_embed(text)))
    except Exception:
        s = next(iter(model.embed([text])))
    return {"indices": s.indices.tolist(), "values": s.values.tolist()}


def warmup() -> None:
    """Pre-load both models (call once at startup to avoid first-query lag)."""
    _get_dense()
    _get_sparse()
