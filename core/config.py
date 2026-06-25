"""
Central configuration.

Values are read from environment variables (.env) as defaults, but the
LLM-related settings are overridable at runtime from the Admin → Settings
page (stored in SQLite via core.db.get_setting / set_setting).

Nothing here imports Streamlit so the module is safe to use from CLI scripts.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


# ── Embeddings ───────────────────────────────────────────────────────────────
# BGE-small (384-dim) dense + Qdrant/bm25 sparse. Same family the original
# project used; runs locally on CPU via fastembed (ONNX), no server needed.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))
SPARSE_MODEL = os.getenv("SPARSE_MODEL", "Qdrant/bm25")
EMBED_THREADS = int(os.getenv("EMBED_THREADS", "0")) or None  # None = let ONNX decide

# ── Qdrant (remote server / Cloud) ───────────────────────────────────────────
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "rag_documents")

# ── Chunking ─────────────────────────────────────────────────────────────────
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "512"))         # in words
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "80"))    # in words

# ── Retrieval ────────────────────────────────────────────────────────────────
RETRIEVE_TOP_K = int(os.getenv("RETRIEVE_TOP_K", "20"))   # candidates per stage
ANSWER_TOP_K = int(os.getenv("ANSWER_TOP_K", "6"))        # chunks fed to the LLM
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "0.0"))

# ── Metadata store ───────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", os.path.join(os.getcwd(), "rag_meta.db"))

# ── Admin gate ───────────────────────────────────────────────────────────────
# Simple shared password to open the Admin panel. Set ADMIN_PASSWORD in .env.
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

# ── Auth / access control ────────────────────────────────────────────────────
# Sign-in is Microsoft/Entra via Streamlit native OIDC ([auth] in secrets).
# Set AUTH_DEV_BYPASS=true ONLY for local development without Entra — it makes
# the signed-in user a full-access dev user (no security trimming). Never set
# this in a deployed/cloud environment.
AUTH_DEV_BYPASS = os.getenv("AUTH_DEV_BYPASS", "false").lower() == "true"

# Sentinel principal added to non-SharePoint docs (web pages, manual uploads)
# so every signed-in user can see admin-curated public knowledge.
ALL_AUTHENTICATED = "__all_authenticated__"
# Sentinel access set used by the dev-bypass user to skip filtering entirely.
DEV_ALL = "__dev_all__"

# ── LLM defaults (overridable in Admin → Settings) ───────────────────────────
LLM_DEFAULTS: dict[str, str] = {
    # provider: "ollama" | "openai" | "azure_openai"
    "llm_provider": os.getenv("LLM_PROVIDER", "ollama"),

    # Ollama
    "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    "ollama_model": os.getenv("OLLAMA_MODEL", "mistral:7b"),

    # OpenAI
    "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
    "openai_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),

    # Azure OpenAI
    "azure_openai_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", ""),
    "azure_openai_api_key": os.getenv("AZURE_OPENAI_API_KEY", ""),
    "azure_openai_api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
    "azure_openai_deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT", ""),
}


@dataclass(frozen=True)
class Settings:
    embedding_model: str = EMBEDDING_MODEL
    embedding_dim: int = EMBEDDING_DIM
    sparse_model: str = SPARSE_MODEL
    embed_threads: int | None = EMBED_THREADS
    qdrant_url: str = QDRANT_URL
    qdrant_api_key: str | None = QDRANT_API_KEY
    qdrant_collection: str = QDRANT_COLLECTION
    chunk_size: int = CHUNK_SIZE
    chunk_overlap: int = CHUNK_OVERLAP
    retrieve_top_k: int = RETRIEVE_TOP_K
    answer_top_k: int = ANSWER_TOP_K
    score_threshold: float = SCORE_THRESHOLD
    db_path: str = DB_PATH
    admin_password: str = ADMIN_PASSWORD


@lru_cache
def get_settings() -> Settings:
    return Settings()


SUPPORTED_EXTENSIONS = {"pdf", "docx", "txt", "md", "html", "htm"}
