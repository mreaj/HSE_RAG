"""
Web source fetcher.

Fetches a registered URL (with SSRF protection + open-redirect re-check),
extracts readable text, and returns (text, content_hash, final_url).
The caller (ui.admin_web) hands the text to core.ingestion.
"""
from __future__ import annotations

import hashlib

import httpx

from core.security import is_safe_url
from core.chunking import extract_html, extract_text

_HEADERS = {"User-Agent": "RAG-Streamlit/1.0 (knowledge aggregator)"}
# file extensions we treat as binary documents rather than HTML pages
_BINARY_EXT = ("pdf", "docx")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_url(url: str) -> tuple[str, str, str]:
    """Return (extracted_text, content_hash, final_url)."""
    safe, err = is_safe_url(url)
    if not safe:
        raise ValueError(f"URL blocked by SSRF check: {err}")

    with httpx.Client(follow_redirects=True, timeout=30.0, headers=_HEADERS) as client:
        r = client.get(url)
        r.raise_for_status()
        final = str(r.url)
        safe2, err2 = is_safe_url(final)
        if not safe2:
            raise ValueError(f"Redirected URL blocked by SSRF check: {err2}")

        ctype = r.headers.get("content-type", "").lower()
        lower = final.lower()
        if "application/pdf" in ctype or lower.endswith(".pdf"):
            pages = extract_text(r.content, "pdf")
            text = "\n\n".join(t for t, _ in pages)
        elif lower.endswith(".docx") or "officedocument.wordprocessingml" in ctype:
            text = extract_text(r.content, "docx")[0][0]
        else:
            text = extract_html(r.text, url=final)

    text = (text or "").strip()
    if not text:
        raise ValueError("No extractable text found at URL")
    return text, content_hash(text), final
