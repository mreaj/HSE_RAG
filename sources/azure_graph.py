"""
Microsoft Graph fetcher — SharePoint document libraries & OneDrive.

Auth: app-only (client credentials) flow via MSAL. You register an app in
Microsoft Entra ID (Azure AD) and supply:
    tenant_id      Directory (tenant) ID
    client_id      Application (client) ID   <- this is the "App ID"
    client_secret  a client secret value

The app needs Microsoft Graph *Application* permissions with admin consent:
    Sites.Read.All        (SharePoint)
    Files.Read.All        (OneDrive / drives)
(Use the .Selected variants if you scope access per-site.)

Then this module:
    * acquires a token for https://graph.microsoft.com/.default
    * resolves a SharePoint site URL -> site id -> default document library
      (or a user's OneDrive)
    * recursively walks folders, downloads supported files, and yields bytes

Returns GraphFile items the caller ingests into Qdrant.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional
from urllib.parse import urlparse

import httpx

from core.config import SUPPORTED_EXTENSIONS

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = ["https://graph.microsoft.com/.default"]


@dataclass
class GraphFile:
    item_id: str
    name: str
    path: str          # human-readable path within the drive
    size: int
    download_url: str
    ext: str


# ── auth ─────────────────────────────────────────────────────────────────────

def acquire_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    import msal
    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )
    result = app.acquire_token_for_client(scopes=SCOPE)
    if "access_token" not in result:
        err = result.get("error_description") or result.get("error") or "unknown error"
        raise RuntimeError(f"Token acquisition failed: {err}")
    return result["access_token"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── resolve drive ────────────────────────────────────────────────────────────

def resolve_site_drive(token: str, site_url: str) -> tuple[str, str]:
    """site_url e.g. https://contoso.sharepoint.com/sites/Safety -> (site_id, drive_id)."""
    parsed = urlparse(site_url)
    hostname = parsed.hostname
    server_path = parsed.path.rstrip("/")  # e.g. /sites/Safety
    with httpx.Client(timeout=30) as client:
        # site id
        site_endpoint = f"{GRAPH}/sites/{hostname}:{server_path}" if server_path else f"{GRAPH}/sites/{hostname}"
        r = client.get(site_endpoint, headers=_headers(token))
        r.raise_for_status()
        site_id = r.json()["id"]
        # default document library (drive)
        r2 = client.get(f"{GRAPH}/sites/{site_id}/drive", headers=_headers(token))
        r2.raise_for_status()
        drive_id = r2.json()["id"]
    return site_id, drive_id


def resolve_user_drive(token: str, user_upn: str) -> str:
    with httpx.Client(timeout=30) as client:
        r = client.get(f"{GRAPH}/users/{user_upn}/drive", headers=_headers(token))
        r.raise_for_status()
        return r.json()["id"]


# ── walk + download ──────────────────────────────────────────────────────────

def _children_url(drive_id: str, folder_path: str) -> str:
    folder_path = (folder_path or "").strip("/")
    if folder_path:
        return f"{GRAPH}/drives/{drive_id}/root:/{folder_path}:/children"
    return f"{GRAPH}/drives/{drive_id}/root/children"


def list_files(token: str, drive_id: str, folder_path: str = "") -> list[GraphFile]:
    """Recursively list supported files under folder_path within a drive."""
    files: list[GraphFile] = []
    _walk(token, drive_id, _children_url(drive_id, folder_path),
          prefix=folder_path.strip("/"), out=files)
    return files


def _walk(token: str, drive_id: str, url: str, prefix: str, out: list[GraphFile]) -> None:
    with httpx.Client(timeout=60) as client:
        next_url: Optional[str] = url
        while next_url:
            r = client.get(next_url, headers=_headers(token))
            r.raise_for_status()
            data = r.json()
            for item in data.get("value", []):
                name = item.get("name", "")
                item_path = f"{prefix}/{name}" if prefix else name
                if "folder" in item:
                    child_url = f"{GRAPH}/drives/{drive_id}/items/{item['id']}/children"
                    _walk(token, drive_id, child_url, item_path, out)
                elif "file" in item:
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    if ext in SUPPORTED_EXTENSIONS:
                        out.append(GraphFile(
                            item_id=item["id"],
                            name=name,
                            path=item_path,
                            size=int(item.get("size", 0)),
                            download_url=item.get("@microsoft.graph.downloadUrl", ""),
                            ext=ext,
                        ))
            next_url = data.get("@odata.nextLink")


def download_file(token: str, drive_id: str, gf: GraphFile) -> bytes:
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        if gf.download_url:
            r = client.get(gf.download_url)  # pre-authenticated short-lived URL
        else:
            r = client.get(f"{GRAPH}/drives/{drive_id}/items/{gf.item_id}/content",
                           headers=_headers(token))
        r.raise_for_status()
        return r.content


# ── high-level helpers used by the admin UI ──────────────────────────────────

def resolve_drive_for_conn(token: str, conn: dict) -> str:
    if conn["resource_type"] == "sharepoint_site":
        if not conn.get("site_url"):
            raise ValueError("SharePoint connection missing site_url")
        _, drive_id = resolve_site_drive(token, conn["site_url"])
        return drive_id
    if conn["resource_type"] == "onedrive_user":
        if not conn.get("user_upn"):
            raise ValueError("OneDrive connection missing user_upn")
        return resolve_user_drive(token, conn["user_upn"])
    raise ValueError(f"Unknown resource_type: {conn['resource_type']}")


def test_connection(conn: dict) -> tuple[bool, str]:
    """Verify auth + drive resolution + a sample listing."""
    try:
        token = acquire_token(conn["tenant_id"], conn["client_id"], conn["client_secret"])
        drive_id = resolve_drive_for_conn(token, conn)
        files = list_files(token, drive_id, conn.get("folder_path", ""))
        return True, f"Connected. Found {len(files)} supported file(s)."
    except httpx.HTTPStatusError as e:
        return False, f"Graph HTTP {e.response.status_code}: {e.response.text[:300]}"
    except Exception as e:
        return False, str(e)


# ── Security trimming: permissions + group membership ────────────────────────
# These power "honor real SharePoint permissions". At ingest time we read each
# file's permission principals; at query time we expand the signed-in user's
# Entra group memberships. A user sees a chunk only if the two sets intersect.

from core.config import ALL_AUTHENTICATED  # noqa: E402


def _collect_identity(identity: dict, out: set) -> None:
    """Pull Entra object IDs (user/group) out of a Graph identitySet."""
    if not identity:
        return
    for key in ("user", "group"):
        ident = identity.get(key)
        if ident and ident.get("id"):
            out.add(ident["id"])
    # siteUser / siteGroup are SharePoint-scoped (not Entra object IDs); we
    # intentionally skip them — better to deny than to over-share. See README.


def get_item_principals(token: str, drive_id: str, item_id: str) -> set[str]:
    """
    Entra principals (user + group object IDs) granted access to a driveItem,
    plus ALL_AUTHENTICATED for org-wide / anonymous sharing links.
    Reads inherited + direct permissions. Fails closed (returns what it can
    resolve; unresolved grants simply aren't added).
    """
    principals: set[str] = set()
    url: str | None = f"{GRAPH}/drives/{drive_id}/items/{item_id}/permissions"
    with httpx.Client(timeout=60) as client:
        while url:
            r = client.get(url, headers=_headers(token))
            r.raise_for_status()
            data = r.json()
            for perm in data.get("value", []):
                link = perm.get("link")
                if link and link.get("scope") in ("organization", "anonymous"):
                    principals.add(ALL_AUTHENTICATED)
                _collect_identity(perm.get("grantedToV2"), principals)
                _collect_identity(perm.get("grantedTo"), principals)
                for ident in perm.get("grantedToIdentitiesV2", []) or []:
                    _collect_identity(ident, principals)
                for ident in perm.get("grantedToIdentities", []) or []:
                    _collect_identity(ident, principals)
            url = data.get("@odata.nextLink")
    return principals


def resolve_oid(token: str, upn_or_email: str) -> Optional[str]:
    """Look up a user's Entra object ID by UPN/email (app-only)."""
    with httpx.Client(timeout=30) as client:
        r = client.get(f"{GRAPH}/users/{upn_or_email}?$select=id", headers=_headers(token))
        if r.status_code == 200:
            return r.json().get("id")
    return None


def user_access_set(token: str, oid: str) -> set[str]:
    """
    The signed-in user's access set: their own object ID + ALL_AUTHENTICATED +
    every Entra group they transitively belong to. Requires application Graph
    permission GroupMember.Read.All (and User.Read.All).
    """
    access: set[str] = {oid, ALL_AUTHENTICATED}
    url: str | None = (
        f"{GRAPH}/users/{oid}/transitiveMemberOf/microsoft.graph.group"
        f"?$select=id&$top=999"
    )
    with httpx.Client(timeout=60) as client:
        while url:
            r = client.get(url, headers=_headers(token))
            r.raise_for_status()
            data = r.json()
            for g in data.get("value", []):
                if g.get("id"):
                    access.add(g["id"])
            url = data.get("@odata.nextLink")
    return access
