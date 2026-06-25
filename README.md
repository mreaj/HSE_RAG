# RAG Console (Streamlit)

A retrieval-augmented chat app with a password-gated admin panel. Vectors AND
all config (registered URLs, Azure connections, LLM settings, document registry)
live in a **persistent Qdrant server / Qdrant Cloud** — so a Streamlit Cloud
reboot loses nothing. Embeddings run **locally** (fastembed, no API needed), and
the answer-generating **LLM is switchable** between Ollama, OpenAI, and Azure OpenAI.

Document sources:
- **Web URLs** — registered in the admin panel, scraped + indexed on demand.
- **Azure / Microsoft Graph** — SharePoint document libraries and user OneDrive,
  authenticated with an Entra ID app (tenant ID, client ID/App ID, client secret).

---

## Deploy on the Streamlit website (no terminal needed)

Everything below happens in a browser: put the code on GitHub, stand up a hosted
Qdrant, register the Entra app, then deploy on Streamlit Community Cloud.

### 1. Put the code on GitHub
- On github.com: **New repository** → create it.
- On the repo page: **Add file → Upload files** → drag in everything from the
  unzipped `rag_streamlit` folder → **Commit changes**.
- `app.py` and `requirements.txt` must sit at the repo **root** (they do in the
  zip). Don't upload `.env` or `.streamlit/secrets.toml`.

### 2. Create a Qdrant Cloud cluster
- At cloud.qdrant.io: sign up → **Create cluster** (free tier is fine).
- Copy the **cluster URL** and create an **API key**. This is where your vectors
  *and* config live permanently.

### 3. Register the Microsoft / Entra app
- Do the steps under **Access control → Set up the Entra app** (below). You'll
  end up with a tenant ID, client ID, and client secret, with the Graph
  permissions consented. You'll set the redirect URI in step 6.

### 4. Deploy on Streamlit Community Cloud
- Go to share.streamlit.io → **Sign in with GitHub** → **Create app** →
  **Deploy a public app from GitHub**.
- Pick your **repository** and **branch**; set **Main file path** to `app.py`.
- Pick a custom subdomain so you know your final URL (e.g.
  `https://my-rag.streamlit.app`).
- Open **Advanced settings → Secrets**, paste the block from step 5, **Deploy**.

### 5. Secrets to paste
On the cloud the LLM must be **OpenAI or Azure OpenAI** (Ollama needs a local
server and won't run there). Use your real values:

```toml
# ── Microsoft sign-in ──
[auth]
redirect_uri = "https://YOUR-APP.streamlit.app/oauth2callback"
cookie_secret = "a-long-random-string"

[auth.microsoft]
client_id = "ENTRA-CLIENT-ID"
client_secret = "ENTRA-CLIENT-SECRET"
server_metadata_url = "https://login.microsoftonline.com/YOUR-TENANT-ID/v2.0/.well-known/openid-configuration"

# ── App config (these become environment variables) ──
QDRANT_URL = "https://YOUR-CLUSTER.aws.cloud.qdrant.io:6333"
QDRANT_API_KEY = "your-qdrant-cloud-key"
QDRANT_COLLECTION = "rag_documents"
ADMIN_PASSWORD = "pick-a-strong-password"

LLM_PROVIDER = "openai"
OPENAI_API_KEY = "sk-..."
OPENAI_MODEL = "gpt-4o-mini"
# Azure OpenAI instead (set LLM_PROVIDER = "azure_openai"):
# AZURE_OPENAI_ENDPOINT = "https://your-resource.openai.azure.com"
# AZURE_OPENAI_API_KEY = "..."
# AZURE_OPENAI_API_VERSION = "2024-08-01-preview"
# AZURE_OPENAI_DEPLOYMENT = "your-deployment"
```

### 6. Point the redirect URIs at your deployed app
The callback must equal your app URL + `/oauth2callback` in **both** places:
- the `redirect_uri` in the secrets above, and
- your Entra app → **Authentication → Web → Redirect URIs**
  (`https://YOUR-APP.streamlit.app/oauth2callback`).

You can edit secrets anytime via the app's **⋮ → Settings → Secrets**; the app
reboots with the new values.

### 7. Use it
Open your app URL → **Log in with Microsoft**. The first question downloads the
embedding model (slower once). Then open **Admin** (enter `ADMIN_PASSWORD`) → add
web URLs and your **Azure** connection → **Sync**. Chat results are trimmed to
what each signed-in user can access in SharePoint.

> **Prefer to run it locally?** In a terminal: `pip install -r requirements.txt`;
> start Qdrant with `docker compose -f docker-compose.qdrant.yml up -d` (or point
> at Qdrant Cloud); copy `.env.example` → `.env` and
> `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml`; then
> `streamlit run app.py`. For local testing without Entra, set
> `AUTH_DEV_BYPASS=true`. Ollama works locally as the LLM.

---

## Admin panel

**Web Sources** — add a URL + label + document type, then *Sync* to scrape and
index it. Re-syncing replaces that source's chunks (no duplicates) and skips
work if the content hasn't changed. Outbound fetches are SSRF-protected
(private/loopback/link-local addresses are refused, including via redirects).

**Azure (SharePoint / OneDrive)** — add a connection, *Test* it, then *Sync* to
walk the drive, download supported files (PDF/DOCX/TXT/MD), and index them.

**Settings** — choose and configure the LLM provider; *Save & test* pings it.

**Index & Stats** — Qdrant health, vector/document counts, direct file upload,
per-document removal, and a full-reset danger zone.

---

## Setting up the Azure / Microsoft Graph app

This is app-only (no signed-in user) access via the client-credentials flow.

1. **Entra admin center → App registrations → New registration.** Give it a
   name; no redirect URI needed. Note the **Directory (tenant) ID** and the
   **Application (client) ID** — that client ID is your "App ID".
2. **Certificates & secrets → New client secret.** Copy the secret *value*
   immediately (you can't see it again).
3. **API permissions → Add a permission → Microsoft Graph → Application
   permissions.** Add:
   - `Sites.Read.All` — to read SharePoint document libraries
   - `Files.Read.All` — to read OneDrive / drive items
   Then **Grant admin consent** for your tenant. (For tighter scoping, use
   `Sites.Selected` and grant the app access to specific sites.)
4. In the app's **Azure** tab, create a connection:
   - **SharePoint site**: paste the site URL, e.g.
     `https://contoso.sharepoint.com/sites/Safety`
   - **OneDrive (user)**: paste the user's UPN, e.g. `user@contoso.com`
   - Optional **folder path** to limit ingestion to a sub-folder.
5. Click **Test**, then **Sync**.

If Test fails with HTTP 403, the permissions usually aren't consented yet, or a
`Sites.Selected` grant for that site is missing.

---

## Access control — honoring SharePoint permissions

Users sign in with **Microsoft (Entra)** via Streamlit's native OIDC, and chat
results are **security-trimmed**: a user can only retrieve a document if they'd
be allowed to open it in SharePoint/OneDrive.

How it works:
- At **sign-in**, Streamlit gives us the user's Entra object ID (`oid`).
- At **ingest**, each SharePoint/OneDrive file's permissions are read via Graph
  and cached on every chunk as `allowed_principals` (the Entra user/group IDs
  granted access, plus a sentinel for org-wide sharing links).
- At **query time**, the user's `oid` is expanded (app-only) into their
  transitive Entra group memberships, and Qdrant only returns chunks whose
  `allowed_principals` intersect that set. **Fails closed** — if anything can't
  be resolved, the user doesn't see the document.
- **Web pages and manual uploads** are tagged `__all_authenticated__`, so they're
  visible to every signed-in user (admin-curated shared knowledge).

### Set up the Entra app (one app serves both sign-in and fetching)

1. **App registration**, single-tenant ("Accounts in this organizational
   directory only"). Note the tenant ID + Application (client) ID.
2. **Authentication → Add a platform → Web**, redirect URI
   `http://localhost:8501/oauth2callback` and your deployed
   `https://<app>.streamlit.app/oauth2callback`.
3. **Certificates & secrets → New client secret** (copy the value).
4. **API permissions → Microsoft Graph → Application permissions**, then
   **Grant admin consent**: `Sites.Read.All`, `Files.Read.All`,
   `GroupMember.Read.All`, `User.Read.All`.
5. Put the client ID/secret + tenant in **two** places — `.streamlit/secrets.toml`
   `[auth.microsoft]` (sign-in) and **Admin → Azure** (fetching + membership).

For local dev without Entra, set `AUTH_DEV_BYPASS=true` (full access, no
trimming — never use in the cloud).

### Known limitations (please read)

Fully mirroring SharePoint permissions is hard. This covers **Entra users,
Entra security groups, and org-wide sharing links**. It does **not** resolve
classic **SharePoint site groups** or some people-picker links not backed by an
Entra object — files relying only on those get no principals and stay **hidden
from non-admins** (safe direction). Cached permissions refresh only on
**re-sync**, so revoked access persists until the next sync. For zero-staleness,
a live per-item check with each user's delegated token is the stricter option —
ask and I can add it.

## How it works

```
            ┌─────────────┐
  URLs ───▶ │  fetchers   │ ──┐
  Azure ──▶ │ web / graph │   │   extract → chunk
            └─────────────┘   ▼
                       ┌──────────────┐   dense + sparse (fastembed, local)
                       │  ingestion   │ ─────────────────────────────────▶ Qdrant
                       └──────────────┘                                     (persistent)
                                                                              │
  question ───▶ hybrid search (dense + BM25, fused with RRF) ◀────────────────┘
                       │
                       ▼
            grounded prompt ─▶ LLM (Ollama / OpenAI / Azure) ─▶ streamed answer + sources
```

Retrieval is **hybrid**: a dense semantic search and a sparse BM25 lexical
search run in parallel and are merged with Reciprocal Rank Fusion, which is more
robust than either alone (good for exact terms, codes, and acronyms as well as
paraphrases).

---

## Files

```
app.py                       Streamlit entry + admin gate + navigation
core/
  config.py                  env-backed settings + defaults
  db.py                      SQLite: settings, web_sources, azure_conns, documents
  embeddings.py              fastembed dense (BGE) + sparse (BM25), lazy-loaded
  chunking.py                pdf/docx/html/txt extraction + word chunking
  security.py                SSRF-safe URL check
  vector_store.py            Qdrant client, hybrid query_points, RRF, upsert/delete
  ingestion.py               extract→chunk→embed→upsert→registry (idempotent)
  llm.py                     switchable provider with streaming generate()
  rag.py                     retrieve → grounded prompt → stream
sources/
  web.py                     URL fetch + extract (SSRF-guarded)
  azure_graph.py             MSAL auth + SharePoint/OneDrive listing & download
ui/
  chat_page.py               chat with streaming + source cards
  admin_web.py               web source CRUD + sync
  admin_azure.py             Graph connection CRUD + test + sync
  admin_settings.py          LLM provider configuration
  admin_stats.py             health, counts, upload, registry, reset
```

## Notes & next steps

- **Secrets**: the Azure client secret is stored in the local SQLite file so the
  admin UI can re-use it for syncs. Keep `rag_meta.db` on a protected volume; for
  production, move secrets to a vault / environment and read them at sync time.
- **Scheduling**: syncs are manual (button) today. To automate, run a small cron
  job that imports `ui.admin_web._sync_one` / `ui.admin_azure._sync_conn` over the
  active sources, or add APScheduler.
- **Multi-tenant / auth**: this uses a single shared admin password. Add real user
  auth (e.g. `streamlit-authenticator` or an SSO proxy) if you need per-user access.
```
