"""
RAG pipeline: retrieve -> build grounded prompt -> stream answer.

answer_stream() is a generator that first does hybrid retrieval, then streams
the LLM answer. It also returns the retrieved chunks via a mutable `sources`
list passed in by the caller (Streamlit needs them to render source cards).
"""
from __future__ import annotations

from typing import Iterator, Optional

from core import vector_store, llm
from core.config import get_settings

_settings = get_settings()


SYSTEM_BASE = (
    "You are a retrieval-augmented assistant. Answer the user's question using "
    "ONLY the document context provided when it is relevant. Cite sources inline "
    "as [title, p.N] using the metadata shown above each excerpt. If the answer "
    "is not contained in the context, say so clearly, then you may answer from "
    "general knowledge and label it as such. Be concise and accurate."
)


def _format_context(chunks: list[vector_store.RetrievedChunk]) -> str:
    blocks = []
    for c in chunks:
        loc = f", p.{c.page_number}" if c.page_number else ""
        blocks.append(f"[Source: {c.title}{loc} | {c.source_ref}]\n{c.text}")
    return "\n\n---\n\n".join(blocks)


def retrieve(query: str, top_k: Optional[int] = None,
             access_set: Optional[set] = None) -> list[vector_store.RetrievedChunk]:
    chunks = vector_store.hybrid_search(query, top_k=top_k or _settings.retrieve_top_k,
                                        access_set=access_set)
    return chunks[: _settings.answer_top_k]


def build_messages(history: list[dict], query: str,
                   chunks: list[vector_store.RetrievedChunk]) -> list[dict]:
    if chunks:
        context = _format_context(chunks)
        system = f"{SYSTEM_BASE}\n\nDOCUMENT CONTEXT:\n{context}"
    else:
        system = (
            "You are a helpful assistant. No documents matched this question, so "
            "answer from general knowledge and note that nothing in the indexed "
            "sources was relevant."
        )
    msgs = [{"role": "system", "content": system}]
    # include short rolling history (last few turns) for continuity
    for m in history[-6:]:
        if m["role"] in ("user", "assistant"):
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": query})
    return msgs


def answer_stream(history: list[dict], query: str,
                  sources_out: list, access_set: Optional[set] = None) -> Iterator[str]:
    chunks = retrieve(query, access_set=access_set)
    sources_out.clear()
    sources_out.extend(chunks)
    messages = build_messages(history, query, chunks)
    yield from llm.generate(messages)
