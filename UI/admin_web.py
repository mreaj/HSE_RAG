"""Admin → Web Sources: register URLs, sync (scrape+ingest), delete."""
from __future__ import annotations

import streamlit as st

from core import db, ingestion
from sources import web

DOC_TYPES = ["general", "policy", "regulatory", "sop", "training", "incident_report"]


def _sync_one(src: dict) -> tuple[bool, str]:
    try:
        text, chash, final_url = web.fetch_url(src["url"])
        if src.get("last_hash") and chash == src["last_hash"]:
            db.update_web_source(src["id"], last_synced_at=db.now_iso(), last_error=None)
            return True, "No change since last sync."
        n = ingestion.ingest_text(
            text, title=src["label"], origin="web", source_ref=final_url,
            doc_type=src.get("doc_type", "general"), origin_id=src["id"],
            doc_id=ingestion.doc_id_for("web", src["id"]),
        )
        db.update_web_source(
            src["id"], last_synced_at=db.now_iso(), last_hash=chash,
            last_chunks=n, last_error=None,
        )
        return True, f"Ingested {n} chunks."
    except Exception as e:
        db.update_web_source(src["id"], last_synced_at=db.now_iso(), last_error=str(e))
        return False, str(e)


def render() -> None:
    st.subheader("Web Sources")
    st.caption("Register URLs to fetch documents/pages from. Sync scrapes the URL, "
               "extracts text, and (re)indexes it into Qdrant.")

    with st.form("add_web", clear_on_submit=True):
        c1, c2 = st.columns([3, 2])
        url = c1.text_input("URL", placeholder="https://example.com/safety-policy")
        label = c2.text_input("Label", placeholder="Safety Policy")
        c3, c4 = st.columns([2, 3])
        doc_type = c3.selectbox("Document type", DOC_TYPES, index=0)
        submitted = c4.form_submit_button("Add source", use_container_width=True)
        if submitted:
            if not url.strip():
                st.error("URL is required.")
            else:
                try:
                    db.add_web_source(url, label, doc_type)
                    st.success(f"Added {url}")
                except Exception as e:
                    st.error(f"Could not add: {e}")

    sources = db.list_web_sources()
    if not sources:
        st.info("No web sources yet.")
        return

    if st.button("Sync all", type="primary"):
        prog = st.progress(0.0)
        for i, src in enumerate(sources, 1):
            ok, msg = _sync_one(src)
            prog.progress(i / len(sources))
        st.success("Sync complete.")
        st.rerun()

    st.divider()
    for src in sources:
        with st.container(border=True):
            top = st.columns([5, 1, 1])
            status = "🟢" if not src.get("last_error") else "🔴"
            top[0].markdown(f"{status} **{src['label']}**  ·  `{src['doc_type']}`")
            top[0].caption(src["url"])
            meta = []
            if src.get("last_synced_at"):
                meta.append(f"synced {src['last_synced_at'][:19].replace('T',' ')}")
            if src.get("last_chunks"):
                meta.append(f"{src['last_chunks']} chunks")
            if meta:
                top[0].caption(" · ".join(meta))
            if src.get("last_error"):
                top[0].caption(f"⚠️ {src['last_error']}")

            if top[1].button("Sync", key=f"sync_{src['id']}"):
                with st.spinner("Fetching + indexing…"):
                    ok, msg = _sync_one(src)
                (st.success if ok else st.error)(msg)
                st.rerun()
            if top[2].button("Delete", key=f"del_{src['id']}"):
                ingestion.delete_document(ingestion.doc_id_for("web", src["id"]))
                db.delete_web_source(src["id"])
                st.rerun()
