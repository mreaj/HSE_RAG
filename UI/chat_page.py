"""Chat page — RAG Q&A with streaming answers and source citations."""
from __future__ import annotations

import streamlit as st

from core import rag, vector_store, llm, auth


def render(access_set: set) -> None:
    st.subheader("Chat")
    caption = f"LLM: **{llm.active_provider()}**  ·  indexed chunks: **{vector_store.count_points()}**"
    st.caption(caption)

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # replay history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("sources"):
                _render_sources(msg["sources"])

    prompt = st.chat_input("Ask a question about your indexed documents…")
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        sources: list = []
        history = st.session_state.messages[:-1]

        def _stream():
            for token in rag.answer_stream(history, prompt, sources, access_set=access_set):
                yield token

        answer = st.write_stream(_stream())
        _render_sources(sources)

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer,
        "sources": [_source_dict(s) for s in sources],
    })


def _source_dict(s) -> dict:
    return {
        "title": s.title, "source_ref": s.source_ref,
        "page_number": s.page_number, "origin": s.origin,
        "score": round(s.score, 4), "text": s.text,
    }


def _render_sources(sources: list) -> None:
    if not sources:
        return
    items = sources if isinstance(sources[0], dict) else [_source_dict(s) for s in sources]
    with st.expander(f"Sources ({len(items)})", expanded=False):
        for i, s in enumerate(items, 1):
            page = f" · p.{s['page_number']}" if s.get("page_number") else ""
            st.markdown(
                f"**{i}. {s['title']}**{page}  ·  *{s.get('origin','')}*  ·  score {s.get('score','')}"
            )
            if s.get("source_ref"):
                st.caption(s["source_ref"])
            snippet = (s.get("text", "") or "")[:400]
            st.markdown(f"> {snippet}{'…' if len(s.get('text','')) > 400 else ''}")
