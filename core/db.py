"""
Metadata store — backed by Qdrant (NOT SQLite).

Everything that is not a document vector (registered URLs, Azure connections,
LLM settings, and the ingested-document registry) is stored as points in a
dedicated Qdrant collection (`<QDRANT_COLLECTION>_config`). Because Qdrant
(Cloud or a server with a volume) persists across restarts, this config
survives Streamlit Community Cloud reboots — unlike the old local SQLite file.

Each config record is one Qdrant point:
    id      = deterministic UUID (so upserts replace, not duplicate)
    vector  = [0.0]  (a 1-dim placeholder; we never vector-search config)
    payload = {"kind": ..., "id"/"key": ..., ...record fields}

Records are read back with client.scroll() filtered by `kind`.

The public function names/signatures are unchanged from the SQLite version,
so ui/* and core/ingestion.py keep working without edits.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
)

from core.config import LLM_DEFAULTS, get_settings
from core import vector_store

_settings = get_settings()
_lock = threading.Lock()

CONFIG_COLLECTION = f"{_settings.qdrant_collection}_config"
_NS = uuid.UUID("6f2a1c00-0000-4000-8000-000000000000")  # stable namespace


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def _client():
    return vector_store.get_client()


def _ensure_config_collection() -> None:
    client = _client()
    existing = {c.name for c in client.get_collections().collections}
    if CONFIG_COLLECTION not in existing:
        client.create_collection(
            collection_name=CONFIG_COLLECTION,
            vectors_config=VectorParams(size=1, distance=Distance.DOT),
        )
        for fld in ("kind", "url", "origin", "origin_id", "doc_id"):
            try:
                client.create_payload_index(CONFIG_COLLECTION, field_name=fld,
                                            field_schema="keyword")
            except Exception:
                pass


def _pid(*parts: str) -> str:
    """Deterministic point id from logical key parts."""
    return str(uuid.uuid5(_NS, ":".join(parts)))


def _put(point_id: str, payload: dict) -> None:
    _client().upsert(
        collection_name=CONFIG_COLLECTION,
        points=[PointStruct(id=point_id, vector=[0.0], payload=payload)],
    )


def _get(point_id: str) -> Optional[dict]:
    res = _client().retrieve(CONFIG_COLLECTION, ids=[point_id], with_payload=True)
    return dict(res[0].payload) if res else None


def _delete(point_id: str) -> None:
    _client().delete(CONFIG_COLLECTION, points_selector=[point_id])


def _scroll(kind: str, extra: Optional[list] = None) -> list[dict]:
    must = [FieldCondition(key="kind", match=MatchValue(value=kind))]
    if extra:
        must.extend(extra)
    out: list[dict] = []
    offset = None
    while True:
        points, offset = _client().scroll(
            collection_name=CONFIG_COLLECTION,
            scroll_filter=Filter(must=must),
            limit=256, offset=offset, with_payload=True,
        )
        out.extend(dict(p.payload) for p in points)
        if offset is None:
            break
    return out


def init_db() -> None:
    _ensure_config_collection()
    for k, v in LLM_DEFAULTS.items():
        if get_setting(k) is None:
            set_setting(k, v)


# ── settings ─────────────────────────────────────────────────────────────────

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    rec = _get(_pid("setting", key))
    return rec["value"] if rec else default


def set_setting(key: str, value: str) -> None:
    with _lock:
        _put(_pid("setting", key), {"kind": "setting", "key": key, "value": value})


def all_settings() -> dict[str, str]:
    return {r["key"]: r.get("value") for r in _scroll("setting")}


# ── web sources ──────────────────────────────────────────────────────────────

def add_web_source(url: str, label: str, doc_type: str = "general") -> dict[str, Any]:
    url = url.strip()
    dup = _scroll("web_source", [FieldCondition(key="url", match=MatchValue(value=url))])
    if dup:
        raise ValueError(f"URL already registered: {url}")
    rec = {
        "kind": "web_source", "id": new_id(), "url": url,
        "label": label.strip() or url, "doc_type": doc_type, "is_active": 1,
        "last_synced_at": None, "last_hash": None, "last_chunks": 0,
        "last_error": None, "created_at": now_iso(),
    }
    with _lock:
        _put(_pid("web_source", rec["id"]), rec)
    return rec


def list_web_sources() -> list[dict[str, Any]]:
    rows = _scroll("web_source")
    return sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)


def get_web_source(sid: str) -> Optional[dict[str, Any]]:
    return _get(_pid("web_source", sid))


def update_web_source(sid: str, **fields) -> None:
    rec = get_web_source(sid)
    if not rec:
        return
    rec.update(fields)
    with _lock:
        _put(_pid("web_source", sid), rec)


def delete_web_source(sid: str) -> None:
    with _lock:
        _delete(_pid("web_source", sid))


# ── azure connections ────────────────────────────────────────────────────────

def add_azure_conn(rec: dict[str, Any]) -> dict[str, Any]:
    rec = {
        "kind": "azure_conn", "id": new_id(), "is_active": 1,
        "last_synced_at": None, "last_files": 0, "last_error": None,
        "created_at": now_iso(), "folder_path": "", **rec,
    }
    with _lock:
        _put(_pid("azure_conn", rec["id"]), rec)
    return rec


def list_azure_conns() -> list[dict[str, Any]]:
    rows = _scroll("azure_conn")
    return sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)


def get_azure_conn(cid: str) -> Optional[dict[str, Any]]:
    return _get(_pid("azure_conn", cid))


def update_azure_conn(cid: str, **fields) -> None:
    rec = get_azure_conn(cid)
    if not rec:
        return
    rec.update(fields)
    with _lock:
        _put(_pid("azure_conn", cid), rec)


def delete_azure_conn(cid: str) -> None:
    with _lock:
        _delete(_pid("azure_conn", cid))


# ── document registry ────────────────────────────────────────────────────────

def upsert_document(doc_id: str, title: str, origin: str, origin_id: Optional[str],
                    source_ref: str, doc_type: str, chunks: int) -> None:
    rec = {
        "kind": "document", "doc_id": doc_id, "title": title, "origin": origin,
        "origin_id": origin_id or "", "source_ref": source_ref,
        "doc_type": doc_type, "chunks": chunks, "updated_at": now_iso(),
    }
    with _lock:
        _put(_pid("document", doc_id), rec)


def list_documents(origin: Optional[str] = None) -> list[dict[str, Any]]:
    extra = [FieldCondition(key="origin", match=MatchValue(value=origin))] if origin else None
    rows = _scroll("document", extra)
    return sorted(rows, key=lambda r: r.get("updated_at") or "", reverse=True)


def delete_document(doc_id: str) -> None:
    with _lock:
        _delete(_pid("document", doc_id))


def document_stats() -> dict[str, Any]:
    docs = list_documents()
    by_origin: dict[str, dict] = {}
    total_chunks = 0
    for d in docs:
        total_chunks += int(d.get("chunks") or 0)
        o = d.get("origin", "")
        slot = by_origin.setdefault(o, {"origin": o, "docs": 0, "chunks": 0})
        slot["docs"] += 1
        slot["chunks"] += int(d.get("chunks") or 0)
    return {
        "total_docs": len(docs),
        "total_chunks": total_chunks,
        "by_origin": list(by_origin.values()),
    }
