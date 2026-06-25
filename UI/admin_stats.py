"""Admin → Stats & Index: health, counts, document registry, upload, reset."""
from __future__ import annotations

import streamlit as st

from core import db, vector_store, ingestion
from core.config import get_settings

_settings = get_settings()
DOC_TYPES = ["general", "policy", "regulatory", "sop", "training", "incident_report"]


def render() -> None:
    st.subheader("Index & Stats")

    health = vector_store.health()
    stats = db.document_stats()
    c1, c2, c3 = st.columns(3)
    c1.metric("Qdrant", "healthy" if health == "healthy" else "down")
    c2.metric("Documents", stats["total_docs"])
    c3.metric("Vectors in Qdrant", vector_store.count_points())
    st.caption(f"Collection `{_settings.qdrant_collection}` @ {_settings.qdrant_url}")
    if health != "healthy":
        st.error(f"Qdrant: {health}")

    if stats["by_origin"]:
        st.write("**By origin**")
        st.dataframe(stats["by_origin"], hide_index=True, use_container_width=True)

    # ── manual upload ────────────────────────────────────────────────────────
    st.divider()
    st.write("**Upload documents directly**")
    files = st.file_uploader(
        "PDF / DOCX / TXT / MD", type=["pdf", "docx", "txt", "md"],
        accept_multiple_files=True,
    )
    up_type = st.selectbox("Document type", DOC_TYPES, key="upload_dt")
    if files and st.button("Ingest uploads", type="primary"):
        prog = st.progress(0.0)
        total = 0
        for i, f in enumerate(files, 1):
            ext = f.name.rsplit(".", 1)[-1].lower()
            try:
                n = ingestion.ingest_bytes(
                    f.getvalue(), ext, title=f.name, origin="upload",
                    source_ref=f.name, doc_type=up_type,
                    doc_id=ingestion.doc_id_for("upload", f.name),
                )
                total += n
            except Exception as e:
                st.warning(f"{f.name}: {e}")
            prog.progress(i / len(files))
        st.success(f"Ingested {total} chunks from {len(files)} file(s).")
        st.rerun()

    # ── document registry ────────────────────────────────────────────────────
    st.divider()
    st.write("**Indexed documents**")
    docs = db.list_documents()
    if not docs:
        st.info("Nothing indexed yet.")
    else:
        for d in docs[:200]:
            row = st.columns([5, 1])
            row[0].markdown(f"**{d['title']}**  ·  *{d['origin']}*  ·  {d['chunks']} chunks")
            row[0].caption(d["source_ref"])
            if row[1].button("Remove", key=f"rm_{d['doc_id']}"):
                ingestion.delete_document(d["doc_id"])
                st.rerun()

    # ── danger zone ──────────────────────────────────────────────────────────
    st.divider()
    with st.expander("⚠️ Danger zone"):
        st.write("Wipe **all** vectors from the Qdrant collection and clear the registry.")
        confirm = st.text_input("Type RESET to confirm")
        if st.button("Reset entire index", type="secondary"):
            if confirm == "RESET":
                vector_store.reset_collection()
                for d in db.list_documents():
                    db.delete_document(d["doc_id"])
                st.success("Index reset.")
                st.rerun()
            else:
                st.error("Type RESET to confirm.")
