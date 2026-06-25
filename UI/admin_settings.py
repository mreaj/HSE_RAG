"""Admin → Settings: choose + configure the LLM provider (Ollama/OpenAI/Azure)."""
from __future__ import annotations

import streamlit as st

from core import db, llm

PROVIDERS = {
    "ollama": "Ollama (local)",
    "openai": "OpenAI",
    "azure_openai": "Azure OpenAI",
}


def render() -> None:
    st.subheader("Settings — Language Model")
    st.caption("Embeddings always run locally (fastembed). Only the answer-generating "
               "LLM is configured here; changes take effect immediately.")

    current = db.get_setting("llm_provider", "ollama")
    provider = st.selectbox(
        "Active provider",
        list(PROVIDERS.keys()),
        index=list(PROVIDERS.keys()).index(current) if current in PROVIDERS else 0,
        format_func=lambda k: PROVIDERS[k],
    )

    s = db.all_settings()
    st.divider()

    if provider == "ollama":
        base = st.text_input("Ollama base URL", s.get("ollama_base_url", "http://localhost:11434"))
        model = st.text_input("Model", s.get("ollama_model", "mistral:7b"),
                              help="e.g. mistral:7b, llama3.1:8b, phi3:mini")
        new = {"ollama_base_url": base, "ollama_model": model}

    elif provider == "openai":
        key = st.text_input("API key", s.get("openai_api_key", ""), type="password")
        model = st.text_input("Model", s.get("openai_model", "gpt-4o-mini"))
        new = {"openai_api_key": key, "openai_model": model}

    else:  # azure_openai
        endpoint = st.text_input(
            "Azure endpoint", s.get("azure_openai_endpoint", ""),
            placeholder="https://my-resource.openai.azure.com")
        key = st.text_input("API key", s.get("azure_openai_api_key", ""), type="password")
        c1, c2 = st.columns(2)
        deployment = c1.text_input("Deployment name", s.get("azure_openai_deployment", ""))
        api_version = c2.text_input("API version", s.get("azure_openai_api_version", "2024-08-01-preview"))
        new = {
            "azure_openai_endpoint": endpoint, "azure_openai_api_key": key,
            "azure_openai_deployment": deployment, "azure_openai_api_version": api_version,
        }

    col1, col2 = st.columns([1, 1])
    if col1.button("Save", type="primary"):
        db.set_setting("llm_provider", provider)
        for k, v in new.items():
            db.set_setting(k, v)
        st.success("Saved.")
    if col2.button("Save & test"):
        db.set_setting("llm_provider", provider)
        for k, v in new.items():
            db.set_setting(k, v)
        with st.spinner("Pinging the model…"):
            ok, msg = llm.test_connection()
        (st.success if ok else st.error)(f"{'OK' if ok else 'Failed'}: {msg}")
