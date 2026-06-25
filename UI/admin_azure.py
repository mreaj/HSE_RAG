"""Admin → Azure (Microsoft Graph): SharePoint / OneDrive connections."""
from __future__ import annotations

import streamlit as st

from core import db, ingestion
from sources import azure_graph as ag

DOC_TYPES = ["general", "policy", "regulatory", "sop", "training", "incident_report"]


def _sync_conn(conn: dict, doc_type: str = "general") -> tuple[bool, str]:
    try:
        token = ag.acquire_token(conn["tenant_id"], conn["client_id"], conn["client_secret"])
        drive_id = ag.resolve_drive_for_conn(token, conn)
        files = ag.list_files(token, drive_id, conn.get("folder_path", ""))
        total_chunks = 0
        unresolved = 0
        prog = st.progress(0.0, text=f"0 / {len(files)} files")
        for i, gf in enumerate(files, 1):
            try:
                # Honor SharePoint permissions: cache the file's allowed principals.
                principals = sorted(ag.get_item_principals(token, drive_id, gf.item_id))
                if not principals:
                    unresolved += 1  # fail closed: only admins (dev bypass) will see it
                data = ag.download_file(token, drive_id, gf)
                n = ingestion.ingest_bytes(
                    data, gf.ext, title=gf.name, origin="azure",
                    source_ref=f"{conn['name']}:/{gf.path}",
                    doc_type=doc_type, origin_id=conn["id"],
                    doc_id=ingestion.doc_id_for("azure", f"{conn['id']}:{gf.item_id}"),
                    allowed_principals=principals,
                )
                total_chunks += n
            except Exception as fe:
                st.warning(f"Skipped {gf.name}: {fe}")
            prog.progress(i / max(1, len(files)), text=f"{i} / {len(files)} files")
        db.update_azure_conn(
            conn["id"], last_synced_at=db.now_iso(),
            last_files=len(files), last_error=None,
        )
        msg = f"Indexed {len(files)} file(s), {total_chunks} chunks."
        if unresolved:
            msg += (f" ⚠️ {unresolved} file(s) had no resolvable Entra permissions "
                    f"(hidden from non-admins — see README on SharePoint groups).")
        return True, msg
    except Exception as e:
        db.update_azure_conn(conn["id"], last_synced_at=db.now_iso(), last_error=str(e))
        return False, str(e)


def render() -> None:
    st.subheader("Azure — SharePoint / OneDrive (Microsoft Graph)")
    st.caption(
        "Connect an Entra ID (Azure AD) app registration to fetch documents. "
        "The app needs **Application** Graph permissions with admin consent: "
        "`Sites.Read.All` (SharePoint) and/or `Files.Read.All` (OneDrive)."
    )

    with st.expander("➕ Add a connection", expanded=not db.list_azure_conns()):
        with st.form("add_azure", clear_on_submit=True):
            name = st.text_input("Connection name", placeholder="Safety SharePoint")
            c1, c2 = st.columns(2)
            tenant_id = c1.text_input("Tenant ID (Directory ID)")
            client_id = c2.text_input("Client ID (Application / App ID)")
            client_secret = st.text_input("Client secret", type="password")

            resource_type = st.radio(
                "Source type", ["sharepoint_site", "onedrive_user"],
                format_func=lambda x: "SharePoint site" if x == "sharepoint_site" else "OneDrive (user)",
                horizontal=True,
            )
            c3, c4 = st.columns(2)
            site_url = c3.text_input(
                "SharePoint site URL",
                placeholder="https://contoso.sharepoint.com/sites/Safety",
                help="Used when source type is SharePoint site.",
            )
            user_upn = c4.text_input(
                "OneDrive user (UPN)", placeholder="user@contoso.com",
                help="Used when source type is OneDrive.",
            )
            folder_path = st.text_input(
                "Folder path (optional)", placeholder="Policies/2025",
                help="Sub-folder within the drive. Leave blank for the whole library.",
            )
            submitted = st.form_submit_button("Save connection", type="primary")
            if submitted:
                missing = [k for k, v in {
                    "name": name, "tenant_id": tenant_id,
                    "client_id": client_id, "client_secret": client_secret,
                }.items() if not v.strip()]
                if missing:
                    st.error(f"Missing: {', '.join(missing)}")
                elif resource_type == "sharepoint_site" and not site_url.strip():
                    st.error("SharePoint site URL is required for that source type.")
                elif resource_type == "onedrive_user" and not user_upn.strip():
                    st.error("OneDrive UPN is required for that source type.")
                else:
                    db.add_azure_conn({
                        "name": name, "tenant_id": tenant_id, "client_id": client_id,
                        "client_secret": client_secret, "resource_type": resource_type,
                        "site_url": site_url.strip() or None,
                        "user_upn": user_upn.strip() or None,
                        "folder_path": folder_path.strip(),
                    })
                    st.success("Connection saved.")
                    st.rerun()

    conns = db.list_azure_conns()
    if not conns:
        st.info("No Azure connections yet.")
        return

    st.divider()
    for conn in conns:
        with st.container(border=True):
            status = "🔴" if conn.get("last_error") else "🟢"
            kind = "SharePoint" if conn["resource_type"] == "sharepoint_site" else "OneDrive"
            target = conn.get("site_url") or conn.get("user_upn") or ""
            st.markdown(f"{status} **{conn['name']}**  ·  *{kind}*")
            st.caption(f"{target}" + (f"  ·  /{conn['folder_path']}" if conn.get("folder_path") else ""))
            st.caption(f"client_id: `{conn['client_id']}`  ·  tenant: `{conn['tenant_id']}`")
            if conn.get("last_synced_at"):
                st.caption(f"last sync {conn['last_synced_at'][:19].replace('T',' ')} · "
                           f"{conn.get('last_files',0)} files")
            if conn.get("last_error"):
                st.caption(f"⚠️ {conn['last_error']}")

            cols = st.columns([1, 1, 2, 1])
            if cols[0].button("Test", key=f"test_{conn['id']}"):
                with st.spinner("Testing Graph connection…"):
                    ok, msg = ag.test_connection(conn)
                (st.success if ok else st.error)(msg)
            doc_type = cols[2].selectbox(
                "Doc type", DOC_TYPES, key=f"dt_{conn['id']}", label_visibility="collapsed")
            if cols[1].button("Sync", key=f"syncaz_{conn['id']}", type="primary"):
                ok, msg = _sync_conn(conn, doc_type)
                (st.success if ok else st.error)(msg)
                st.rerun()
            if cols[3].button("Delete", key=f"delaz_{conn['id']}"):
                # remove this connection's docs from Qdrant + registry
                for d in db.list_documents(origin="azure"):
                    if d["origin_id"] == conn["id"]:
                        ingestion.delete_document(d["doc_id"])
                db.delete_azure_conn(conn["id"])
                st.rerun()
