"""
Regression tests for llm.generate_with_fallback's chain-advancement
timing. These mock llm.generate() directly — no network, no Ollama,
no deepseek key required — to verify call counts and ordering rather
than actual model output.

Bug being guarded against: generate_with_fallback used to call
safe_generate() per chain entry, which retries the SAME model twice
before reporting failure. For a 2-entry chain like
[("local", "mistral"), ("deepseek", "deepseek-chat")], that meant
mistral could be attempted twice (burning up to 2x its timeout) before
deepseek was ever reached. Reproduced from a production timeout where
the role="reasoning" chain never appeared to reach deepseek within the
client's request timeout.
"""

from unittest.mock import patch

import llm


def test_fallback_chain_tries_each_entry_exactly_once_on_failure():
    """
    Every entry in FALLBACK_CHAIN["reasoning"] should be attempted
    exactly once when all fail — NOT twice each (the old safe_generate
    behaviour), which is what let mistral alone burn the whole budget.
    """
    calls = []

    def fake_generate(provider, prompt, model, deepseek_api_key=None):
        calls.append((provider, model))
        return None  # simulate every attempt failing

    with patch.object(llm, "generate", side_effect=fake_generate):
        result = llm.generate_with_fallback("reasoning", "some prompt")

    # mistral (chain), deepseek-chat (chain) — each exactly once.
    # mistral may appear a second time only via the final "forced"
    # safety net for chains that DON'T already include local/mistral —
    # "reasoning"'s chain already includes it, so it must NOT repeat.
    assert calls == [("local", "mistral"), ("deepseek", "deepseek-chat")]
    assert result["model"] == "none"


def test_fallback_chain_advances_to_deepseek_immediately_on_mistral_failure():
    """
    The chain must advance to deepseek as soon as mistral fails — not
    retry mistral first. This is the core of the bug: the old code
    retried the failing entry before ever trying the next one.
    """
    calls = []

    def fake_generate(provider, prompt, model, deepseek_api_key=None):
        calls.append((provider, model))
        if provider == "local" and model == "mistral":
            return None  # mistral fails (e.g. timeout)
        return {"text": "answer from deepseek", "model": model, "provider": provider}

    with patch.object(llm, "generate", side_effect=fake_generate):
        result = llm.generate_with_fallback("reasoning", "some prompt")

    # Exactly one mistral attempt before deepseek — not two.
    assert calls == [("local", "mistral"), ("deepseek", "deepseek-chat")]
    assert result["provider"] == "deepseek"
    assert result["fallback_used"] is True


def test_fallback_chain_returns_immediately_on_first_success_no_retry_calls():
    calls = []

    def fake_generate(provider, prompt, model, deepseek_api_key=None):
        calls.append((provider, model))
        return {"text": "first try works", "model": model, "provider": provider}

    with patch.object(llm, "generate", side_effect=fake_generate):
        result = llm.generate_with_fallback("reasoning", "some prompt")

    assert calls == [("local", "mistral")]
    assert result["fallback_used"] is False


def test_fallback_chain_without_mistral_forces_single_final_mistral_attempt():
    """
    "extract" role's chain is [("local", "mistral")] already, so this
    targets a role whose chain does NOT include local/mistral to
    exercise the "forced final attempt" safety net — and confirms that
    safety net is also a single attempt, not a double-retry.
    """
    calls = []

    def fake_generate(provider, prompt, model, deepseek_api_key=None):
        calls.append((provider, model))
        if (provider, model) == ("local", "mistral"):
            return {"text": "forced mistral works", "model": model, "provider": provider}
        return None

    fake_chain = {"weird_role": [("deepseek", "deepseek-chat")]}
    with patch.object(llm, "FALLBACK_CHAIN", fake_chain):
        with patch.object(llm, "generate", side_effect=fake_generate):
            result = llm.generate_with_fallback("weird_role", "some prompt")

    assert calls == [("deepseek", "deepseek-chat"), ("local", "mistral")]
    assert result["fallback_used"] is True
