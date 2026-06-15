"""
Query router.

Classifies an incoming query into one of four roles:
  extract   → structured list/checklist extraction  → mistral
  fast      → short factual lookup                  → phi  (mistral fallback)
  accurate  → multi-sentence explanation             → mistral (deepseek fallback)
  reasoning → causal / comparative / multi-hop       → mistral (deepseek fallback)

The fallback chain is enforced in llm.generate_with_fallback().
"""

# Primary (provider, model) per role
MODEL_MAP: dict[str, tuple[str, str]] = {
    "extract":   ("local", "mistral"),
    "fast":      ("local", "phi"),
    "accurate":  ("local", "mistral"),
    "reasoning": ("local", "mistral"),   # deepseek used as fallback via llm.py
}

# Keywords that trigger each role (checked in priority order)
_REASONING_KW = [
    "why", "explain", "how does", "what causes", "reason for",
    "difference between", "compare", "relationship between",
    "what happens when", "impact of", "effect of", "works with",
]

_EXTRACT_KW = [
    "checklist", "steps to", "procedure", "list", "instructions",
    "give me", "show me the steps", "what are the steps",
    "how to", "walkthrough", "sign off",
]

_FAST_KW = [
    "what is", "define", "default", "credentials", "password",
    "username", "ip address", "version", "what does", "name of",
    "introduction",
]


def requires_multi_hop(query: str) -> bool:
    """Detect queries that likely span more than one document section."""
    q = query.lower()
    indicators = [
        "compared to", "relationship between", "works with",
        "integration", "interact", "together with", "depends on",
        "how does", "and verify",
    ]
    return any(kw in q for kw in indicators)


def route_model(query: str) -> tuple[str, tuple[str, str]]:
    """
    Returns: (role, (provider, model))

    Usage in main.py:
        role, (provider, model) = route_model(q)
    """
    q = query.lower().strip()

    if any(kw in q for kw in _REASONING_KW) or requires_multi_hop(q):
        return "reasoning", MODEL_MAP["reasoning"]

    if any(kw in q for kw in _EXTRACT_KW):
        return "extract", MODEL_MAP["extract"]

    if any(kw in q for kw in _FAST_KW):
        return "fast", MODEL_MAP["fast"]

    return "accurate", MODEL_MAP["accurate"]