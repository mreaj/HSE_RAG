"""
Ingestion orchestrator.

ingest_bytes / ingest_text:
  1. extract text -> pages
  2. chunk
  3. delete any prior vectors for this doc_id (idempotent re-sync)
  4. embed + upsert to Qdrant
  5. record in the SQLite document registry

doc_id is deterministic per origin so re-syncing the same source replaces
its chunks instead of duplicating them.
"""
from __future__ import annotations

import uuid
from typing import Optional

from core import db, vector_store
from core.config import ALL_AUTHENTICATED
from core.chunking import extract_text, pages_to_chunks, chunk_text


def doc_id_for(origin: str, key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{origin}:{key}"))


def _store(doc_id: str, title: str, origin: str, origin_id: Optional[str],
           source_ref: str, doc_type: str, chunks: list[dict],
           allowed_principals: Optional[list] = None) -> int:
    # idempotent: clear old vectors for this doc first
    vector_store.delete_by_doc_id(doc_id)
    if not chunks:
        db.upsert_document(doc_id, title, origin, origin_id, source_ref, doc_type, 0)
        return 0

    principals = list(allowed_principals) if allowed_principals is not None else [ALL_AUTHENTICATED]
    payload_chunks = []
    for ch in chunks:
        payload_chunks.append({
            "text": ch["text"],
            "payload": {
                "doc_id": doc_id,
                "title": title,
                "origin": origin,
                "origin_id": origin_id or "",
                "source_ref": source_ref,
                "doc_type": doc_type,
                "page_number": ch.get("page_number"),
                "allowed_principals": principals,
            },
        })
    n = vector_store.upsert_chunks(payload_chunks)
    db.upsert_document(doc_id, title, origin, origin_id, source_ref, doc_type, n)
    return n


def ingest_bytes(file_bytes: bytes, ext: str, *, title: str, origin: str,
                 source_ref: str, doc_type: str = "general",
                 origin_id: Optional[str] = None,
                 doc_id: Optional[str] = None,
                 allowed_principals: Optional[list] = None) -> int:
    pages = extract_text(file_bytes, ext)
    chunks = pages_to_chunks(pages)
    did = doc_id or doc_id_for(origin, source_ref)
    return _store(did, title, origin, origin_id, source_ref, doc_type, chunks, allowed_principals)


def ingest_text(text: str, *, title: str, origin: str, source_ref: str,
                doc_type: str = "general", origin_id: Optional[str] = None,
                doc_id: Optional[str] = None,
                allowed_principals: Optional[list] = None) -> int:
    chunks = chunk_text(text, page=None)
    did = doc_id or doc_id_for(origin, source_ref)
    return _store(did, title, origin, origin_id, source_ref, doc_type, chunks, allowed_principals)


def delete_document(doc_id: str) -> None:
    vector_store.delete_by_doc_id(doc_id)
    db.delete_document(doc_id)
