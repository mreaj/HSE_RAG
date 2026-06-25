from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, SparseVectorParams, SparseIndexParams,
    PointStruct, SparseVector,
    Filter, FieldCondition, MatchValue, MatchAny, FilterSelector,
)

from core.config import get_settings, DEV_ALL
from core import embeddings

_settings = get_settings()

DENSE = "dense"
SPARSE = "bm25"

_client: Optional[QdrantClient] = None


# ✅ Helper to clean URL
def _get_host(url: str) -> str:
    return url.replace("https://", "").replace("http://", "").strip().rstrip("/")


@dataclass
class RetrievedChunk:
    text: str
    title: str
    source_ref: str
    doc_id: str
    page_number: Optional[int]
    origin: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)



def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(
            url=_settings.qdrant_url,
            api_key=_settings.qdrant_api_key,
            timeout=30,
        )
    return _client

def _ensure_collection(client: QdrantClient) -> None:
    try:
        collections = client.get_collections()
        existing = {c.name for c in collections.collections}
    except Exception as e:
        # ✅ DO NOT crash app
        print(f"⚠️ Qdrant not reachable: {e}")
        return

    name = _settings.qdrant_collection

    if name not in existing:
        try:
            client.create_collection(
                collection_name=name,
                vectors_config={
                    DENSE: VectorParams(
                        size=_settings.embedding_dim,
                        distance=Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    SPARSE: SparseVectorParams(index=SparseIndexParams())
                },
            )

            for fld in ("doc_id", "origin", "allowed_principals"):
                try:
                    client.create_payload_index(
                        name,
                        field_name=fld,
                        field_schema="keyword"
                    )
                except Exception:
                    pass  # ✅ ignore index errors

        except Exception as e:
            print(f"⚠️ Failed to create collection: {e}")


# ── UPSERT ─────────────────────────────────────────────────────────

def upsert_chunks(chunks: list[dict], batch: int = 32) -> int:
    if not chunks:
        return 0

    client = get_client()
    name = _settings.qdrant_collection
    total = 0

    for start in range(0, len(chunks), batch):
        slice_ = chunks[start:start + batch]

        texts = [c["text"] for c in slice_]
        dense_vecs = embeddings.embed_texts(texts)
        sparse_vecs = embeddings.embed_sparse_docs(texts)

        points = []
        for c, d, s in zip(slice_, dense_vecs, sparse_vecs):
            points.append(PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    DENSE: d,
                    SPARSE: SparseVector(
                        indices=s["indices"],
                        values=s["values"]
                    ),
                },
                payload={**c["payload"], "text": c["text"]},
            ))

        client.upsert(collection_name=name, points=points)
        total += len(points)

    return total


# ── DELETE ─────────────────────────────────────────────────────────

def delete_by_doc_id(doc_id: str) -> None:
    client = get_client()

    client.delete(
        collection_name=_settings.qdrant_collection,
        points_selector=FilterSelector(
            filter=Filter(
                must=[
                    FieldCondition(
                        key="doc_id",
                        match=MatchValue(value=doc_id),
                    )
                ]
            )
        ),
    )


def reset_collection() -> None:
    client = get_client()
    name = _settings.qdrant_collection

    try:
        client.delete_collection(name)
    except Exception:
        pass

    _ensure_collection(client)


# ── SEARCH ─────────────────────────────────────────────────────────

def _rrf(*ranked_lists, k: int = 60) -> list:
    scores: dict[str, float] = {}
    objs: dict[str, Any] = {}

    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked):
            key = str(hit.id)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            objs[key] = hit

    return sorted(objs.values(), key=lambda h: scores[str(h.id)], reverse=True)


def hybrid_search(
    query: str,
    top_k: Optional[int] = None,
    access_set: Optional[set] = None,
) -> list[RetrievedChunk]:

    client = get_client()
    name = _settings.qdrant_collection
    top_k = top_k or _settings.retrieve_top_k

    query_filter = None
    if access_set is not None and DEV_ALL not in access_set:
        principals = [p for p in access_set if p]
        principals = principals or ["__none__"]

        query_filter = Filter(
            must=[
                FieldCondition(
                    key="allowed_principals",
                    match=MatchAny(any=principals),
                )
            ]
        )

    dense_vec = embeddings.embed_query(query)
    sparse_vec = embeddings.embed_sparse_query(query)

    try:
        dense_hits = client.query_points(
            collection_name=name,
            query=dense_vec,
            using=DENSE,
            limit=top_k,
            with_payload=True,
            query_filter=query_filter,
        ).points
    except Exception:
        dense_hits = []

    try:
        sparse_hits = client.query_points(
            collection_name=name,
            query=SparseVector(
                indices=sparse_vec["indices"],
                values=sparse_vec["values"],
            ),
            using=SPARSE,
            limit=top_k,
            with_payload=True,
            query_filter=query_filter,
        ).points
    except Exception:
        sparse_hits = []

    fused = _rrf(dense_hits, sparse_hits)

    out: list[RetrievedChunk] = []
    seen: set[str] = set()

    for hit in fused:
        p = hit.payload or {}
        key = (p.get("doc_id", ""), p.get("page_number"), p.get("text", "")[:80])

        sk = repr(key)
        if sk in seen:
            continue
        seen.add(sk)

        out.append(
            RetrievedChunk(
                text=p.get("text", ""),
                title=p.get("title", "Untitled"),
                source_ref=p.get("source_ref", ""),
                doc_id=p.get("doc_id", ""),
                page_number=p.get("page_number"),
                origin=p.get("origin", ""),
                score=float(getattr(hit, "score", 0.0) or 0.0),
                payload=p,
            )
        )

    return out[:top_k]


# ── UTILITIES ─────────────────────────────────────────────────────

def count_points() -> int:
    try:
        return get_client().count(_settings.qdrant_collection).count
    except Exception:
        return 0


def health() -> str:
    try:
        get_client().get_collections()
        return "healthy"
    except Exception as e:
        return f"unreachable: {e}"
