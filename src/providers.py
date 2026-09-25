"""Which LLM providers this install can reach, and each one's default model.

Read by the answer pipeline (main.query), the console's provider pickers and
the FAQ drafting routes, so it lives outside all of them.
"""

import os

import keystore
from runtime_config import get_settings


# Sensible default per provider when an override provider is chosen but
# no specific model is typed — mirrors the retired AdminPanel.jsx's
# curated model lists.
# Default model when an override provider is chosen but no model is typed.
#
# deepseek was pinned to "deepseek-chat" here, which DeepSeek RETIRED on
# 24 July 2026 — so every console-initiated generation with the DeepSeek
# provider selected failed with "returned nothing", including "generate
# questions". The escalation path in query() already learned this and reads
# ONLINE_DEEPSEEK_MODEL; read it here too rather than keeping a second copy
# of a value that goes stale.
_PROVIDER_STATIC_MODEL = {
    "local": "mistral",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
}


def _default_model_for(provider: str) -> str:
    if provider == "deepseek":
        return os.getenv("ONLINE_DEEPSEEK_MODEL", "deepseek-v4-flash")
    return _PROVIDER_STATIC_MODEL.get(provider, "")


# Which providers this install can actually reach. An online provider needs
# its key in the environment; local needs Ollama warmed. The console builds
# its provider pickers from this so it cannot offer a provider that is
# guaranteed to fail — the old hardcoded list offered all four regardless.
_PROVIDER_LABEL = {
    "local": "Local (Ollama)",
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "anthropic": "Claude",
}


def _available_providers() -> list[dict]:
    out = []
    if get_settings().get("local_models_loaded"):
        out.append({"key": "local", "label": _PROVIDER_LABEL["local"],
                    "default_model": _default_model_for("local")})
    for key in ("deepseek", "openai", "anthropic"):
        if keystore.has_key(key):
            # The operator's name for the key (keystore.label_for), so the
            # Test chat picker says "Company keys", not "OpenAI".
            out.append({"key": key, "label": keystore.label_for(key),
                        "default_model": _default_model_for(key)})
    return out
