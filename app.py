"""
RAG Streamlit app — entry point.

    streamlit run app.py

Layout:
  • Chat            (open to everyone)
  • Admin           (password-gated): Web Sources, Azure, Settings, Index & Stats

Vectors live in a remote Qdrant (QDRANT_URL). Metadata lives in local SQLite.
Embeddings run locally via fastembed. The LLM is switchable in Admin → Settings.
"""
from __future__ import annotations

import streamlit as st

from core import db
from core.config import get_settings

_settings = get_settings()

st.set_page_config(page_title="RAG Console", page_icon="📚", layout="wide")


@st.cache_resource
def _bootstrap():
    db.init_db()
    return True


_bootstrap()


# ── admin auth ───────────────────────────────────────────────────────────────

def _admin_gate() -> bool:
    if st.session_state.get("is_admin"):
        return True
    st.subheader("Admin login")
    pw = st.text_input("Admin password", type="password")
    if st.button("Unlock"):
        if pw == _settings.admin_password:
            st.session_state.is_admin = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.caption("Set ADMIN_PASSWORD in your .env (default is `admin`).")
    return False


# ── navigation ───────────────────────────────────────────────────────────────

st.sidebar.title("📚 RAG Console")

from core import auth

user = auth.require_login()
access_set = auth.access_set_for(user)

st.sidebar.write(f"Signed in as **{user.get('name') or user.get('email')}**")
auth.logout_button()
st.sidebar.divider()

section = st.sidebar.radio("Go to", ["Chat", "Admin"], label_visibility="collapsed")

if st.sidebar.button("Clear chat"):
    st.session_state.messages = []

st.sidebar.divider()
st.sidebar.caption(f"Qdrant: {_settings.qdrant_url}")
st.sidebar.caption(f"Collection: {_settings.qdrant_collection}")

if section == "Chat":
    from ui import chat_page
    chat_page.render(access_set)

else:
    if _admin_gate():
        tab = st.sidebar.radio(
            "Admin",
            ["Web Sources", "Azure (SharePoint/OneDrive)", "Settings", "Index & Stats"],
        )
        if st.sidebar.button("Log out admin"):
            st.session_state.is_admin = False
            st.rerun()

        if tab == "Web Sources":
            from ui import admin_web
            admin_web.render()
        elif tab.startswith("Azure"):
            from ui import admin_azure
            admin_azure.render()
        elif tab == "Settings":
            from ui import admin_settings
            admin_settings.render()
        else:
            from ui import admin_stats
            admin_stats.render()
