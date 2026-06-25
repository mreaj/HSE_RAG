"""
Switchable LLM provider.

Reads the active provider + credentials from the SQLite settings table
(Admin -> Settings), so it can be changed at runtime without editing code:

  - ollama        POST {base_url}/api/chat  (local, default)
  - openai        OpenAI Chat Completions
  - azure_openai  Azure OpenAI Chat Completions (deployment-based)

generate() yields response tokens as they stream in. test_connection()
does a tiny round-trip so the admin can verify credentials.
"""
from __future__ import annotations

import json
from typing import Iterator

import httpx

from core import db


def _cfg() -> dict[str, str]:
    s = db.all_settings()
    return s


def active_provider() -> str:
    return db.get_setting("llm_provider", "ollama")


# ── unified streaming generate ───────────────────────────────────────────────

def generate(messages: list[dict], temperature: float = 0.2) -> Iterator[str]:
    provider = active_provider()
    if provider == "ollama":
        yield from _ollama_stream(messages, temperature)
    elif provider == "openai":
        yield from _openai_stream(messages, temperature, azure=False)
    elif provider == "azure_openai":
        yield from _openai_stream(messages, temperature, azure=True)
    else:
        yield f"[LLM error] unknown provider '{provider}'"


def _ollama_stream(messages: list[dict], temperature: float) -> Iterator[str]:
    cfg = _cfg()
    base = cfg.get("ollama_base_url", "http://localhost:11434").rstrip("/")
    model = cfg.get("ollama_model", "mistral:7b")
    payload = {"model": model, "messages": messages, "stream": True,
               "options": {"temperature": temperature}}
    try:
        with httpx.Client(timeout=300) as client:
            with client.stream("POST", f"{base}/api/chat", json=payload) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    tok = data.get("message", {}).get("content")
                    if tok:
                        yield tok
                    if data.get("done"):
                        break
    except Exception as e:
        yield f"\n\n[Ollama error: {e}]"


def _openai_client(azure: bool):
    cfg = _cfg()
    if azure:
        from openai import AzureOpenAI
        return AzureOpenAI(
            azure_endpoint=cfg.get("azure_openai_endpoint", ""),
            api_key=cfg.get("azure_openai_api_key", ""),
            api_version=cfg.get("azure_openai_api_version", "2024-08-01-preview"),
        ), cfg.get("azure_openai_deployment", "")
    else:
        from openai import OpenAI
        return OpenAI(api_key=cfg.get("openai_api_key", "")), cfg.get("openai_model", "gpt-4o-mini")


def _openai_stream(messages: list[dict], temperature: float, azure: bool) -> Iterator[str]:
    try:
        client, model = _openai_client(azure)
        stream = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as e:
        label = "Azure OpenAI" if azure else "OpenAI"
        yield f"\n\n[{label} error: {e}]"


# ── connection test ──────────────────────────────────────────────────────────

def test_connection() -> tuple[bool, str]:
    msgs = [{"role": "user", "content": "Reply with the single word: ok"}]
    try:
        out = "".join(generate(msgs)).strip()
        if out.startswith("[") and "error" in out.lower():
            return False, out
        return True, out[:120] or "(empty response)"
    except Exception as e:
        return False, str(e)
