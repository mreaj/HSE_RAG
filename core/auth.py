"""
Authentication + authorization.

Authentication: Microsoft/Entra via Streamlit native OIDC (`st.login`).
  st.user gives us the user's `oid` (Entra object ID), email, and tenant.

Authorization (security trimming): we compute the user's *access set* —
their object ID + ALL_AUTHENTICATED + their transitive Entra group IDs —
using app-only Graph (the Azure connection's app credentials, same tenant).
Retrieval is then filtered in Qdrant to chunks whose cached
`allowed_principals` intersect this set. Fails closed.

Local development without Entra: set AUTH_DEV_BYPASS=true to act as a
full-access dev user (no trimming). Never use that in the cloud.
"""
from __future__ import annotations

import streamlit as st

from core import db
from core.config import AUTH_DEV_BYPASS, ALL_AUTHENTICATED, DEV_ALL
from sources import azure_graph as ag


def is_configured() -> bool:
    try:
        return bool(st.secrets.get("auth"))
    except Exception:
        return False


def current_user() -> dict | None:
    if AUTH_DEV_BYPASS:
        return {"oid": "__dev__", "email": "dev@local", "name": "Dev (bypass)", "dev": True}
    try:
        if not st.user.is_logged_in:
            return None
    except Exception:
        return None
    return {
        "oid": st.user.get("oid"),
        "email": st.user.get("email") or st.user.get("preferred_username"),
        "name": st.user.get("name"),
        "tid": st.user.get("tid"),
        "dev": False,
    }


def require_login() -> dict:
    """Render a login gate and stop the run if not signed in. Returns the user."""
    user = current_user()
    if user:
        return user
    st.header("This app is private")
    if not is_configured():
        st.error(
            "Microsoft sign-in isn't configured. Add an `[auth]` / `[auth.microsoft]` "
            "section to your secrets (see README), or set `AUTH_DEV_BYPASS=true` for "
            "local development."
        )
        st.stop()
    st.write("Please sign in with your Microsoft account to continue.")
    st.button("Log in with Microsoft", type="primary", on_click=st.login, args=("microsoft",))
    st.stop()


def logout_button() -> None:
    if AUTH_DEV_BYPASS:
        return
    st.button("Log out", on_click=st.logout)


def _directory_creds() -> tuple[str, str, str] | None:
    """App credentials used for tenant-wide Graph calls (membership lookup)."""
    t = db.get_setting("dir_tenant_id")
    c = db.get_setting("dir_client_id")
    s = db.get_setting("dir_client_secret")
    if t and c and s:
        return t, c, s
    for conn in db.list_azure_conns():
        if conn.get("is_active", 1):
            return conn["tenant_id"], conn["client_id"], conn["client_secret"]
    return None


def access_set_for(user: dict) -> set[str]:
    """
    Compute (and session-cache) the user's access set. On any failure we fail
    CLOSED: the user only gets their own ID + ALL_AUTHENTICATED, never a wildcard.
    """
    if user.get("dev"):
        return {DEV_ALL}  # dev bypass: no filtering

    if (st.session_state.get("_access_oid") == user.get("oid")
            and st.session_state.get("_access_set")):
        return set(st.session_state["_access_set"])

    fallback = {user.get("oid") or "", ALL_AUTHENTICATED}
    creds = _directory_creds()
    if not creds:
        result = fallback
    else:
        try:
            token = ag.acquire_token(*creds)
            oid = user.get("oid")
            if not oid and user.get("email"):
                oid = ag.resolve_oid(token, user["email"])
            result = ag.user_access_set(token, oid) if oid else {ALL_AUTHENTICATED}
        except Exception:
            result = fallback

    st.session_state["_access_oid"] = user.get("oid")
    st.session_state["_access_set"] = list(result)
    return result
