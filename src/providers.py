"""Which LLM providers this install can reach, and each one's default model.

Read by the answer pipeline (main.query), the console's provider pickers and
the FAQ drafting routes, so it lives outside all of them.
"""

import keystore
from runtime_config import get_settings


def _default_model_for(provider: str) -> str:
    """Default model when an override provider is chosen but no model is
    typed: the provider's configured model (ONLINE_<PROVIDER>_MODEL, which
    the console writes), else keystore's floor for that provider.

    keystore.get_model is the ONE place an online provider's default lives.
    This file used to keep its own table, and so did llm.py and router.py;
    the copies went stale separately (deepseek pinned to an alias DeepSeek
    retired on 24 July 2026, then "gpt-4o-mini" sent to an on-prem gateway
    that does not have it, 2026-09-27), and each time every console
    generation and backup escalation on that slot failed."""
    try:
        return keystore.get_model(provider)
    except Exception:                    # "local", or a provider keystore does not know
        return "mistral" if provider == "local" else ""


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
        if keystore.has_key(key) and keystore.may(key, "answers"):
            # The operator's name for the key (keystore.label_for), so the
            # Test chat picker says "Company keys", not "OpenAI".
            out.append({"key": key, "label": keystore.label_for(key),
                        "default_model": _default_model_for(key)})
    return out
