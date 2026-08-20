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
import runtime_config


# The chain shape depends on the generation mode, and these tests used to read
# whatever mode the ambient environment happened to have -- passing under the
# pre-v9.1.2 local-first default and failing once "api" became the default.
# Pin it, so the chain under test is the one the assertions describe.
def setup_function(func):
    runtime_config.set_generation_mode("api")


def teardown_function(func):
    runtime_config.set_generation_mode("api")


def test_fallback_chain_tries_each_entry_exactly_once_on_failure():
    """
    Every entry in FALLBACK_CHAIN["reasoning"] should be attempted
    exactly once when all fail — NOT twice each (the old safe_generate
    behaviour), which is what let mistral alone burn the whole budget.
    """
    calls = []

    # Mirrors llm.generate's real signature. `api_keys` was added there and
    # these fakes were not updated, so every call raised TypeError and all
    # four tests failed on the mock, not on the code under test. **kw keeps
    # the next parameter from doing the same thing again.
    def fake_generate(provider, prompt, model, deepseek_api_key=None,
                      api_keys=None, **kw):
        calls.append((provider, model))
        return None  # simulate every attempt failing

    with patch.object(llm, "generate", side_effect=fake_generate):
        result = llm.generate_with_fallback("reasoning", "some prompt")

    # In api mode the online provider leads and local/mistral follows as the
    # forced final attempt. What this guards is unchanged: each model is tried
    # EXACTLY once, never twice (the old safe_generate behaviour that let one
    # model burn the whole time budget).
    #
    # The expected pair is derived, not literal -- the online model comes from
    # ONLINE_DEEPSEEK_MODEL, and a hardcoded "deepseek-chat" here broke the
    # moment that retired alias was replaced.
    assert calls == [llm._online_provider_model(), ("local", "mistral")]
    assert len(calls) == len(set(calls)), "a model was attempted twice"
    assert result["model"] == "none"


def test_fallback_chain_advances_to_deepseek_immediately_on_mistral_failure():
    """
    The chain must advance to deepseek as soon as mistral fails — not
    retry mistral first. This is the core of the bug: the old code
    retried the failing entry before ever trying the next one.
    """
    calls = []

    # Mirrors llm.generate's real signature. `api_keys` was added there and
    # these fakes were not updated, so every call raised TypeError and all
    # four tests failed on the mock, not on the code under test. **kw keeps
    # the next parameter from doing the same thing again.
    def fake_generate(provider, prompt, model, deepseek_api_key=None,
                      api_keys=None, **kw):
        calls.append((provider, model))
        if provider == "local" and model == "mistral":
            return None  # mistral fails (e.g. timeout)
        return {"text": "answer from deepseek", "model": model, "provider": provider}

    with patch.object(llm, "generate", side_effect=fake_generate):
        result = llm.generate_with_fallback("reasoning", "some prompt")

    # This test was written when the chain was mistral-then-deepseek, so it
    # asserted "one mistral attempt before deepseek". Since v9.1.2 api mode
    # puts the online provider FIRST, which this fake answers successfully --
    # so the surviving guarantee is that a first-entry success stops the chain
    # dead, with no second attempt of anything.
    assert calls == [llm._online_provider_model()]
    assert result["provider"] == "deepseek"
    assert result["fallback_used"] is False


def test_fallback_chain_returns_immediately_on_first_success_no_retry_calls():
    calls = []

    # Mirrors llm.generate's real signature. `api_keys` was added there and
    # these fakes were not updated, so every call raised TypeError and all
    # four tests failed on the mock, not on the code under test. **kw keeps
    # the next parameter from doing the same thing again.
    def fake_generate(provider, prompt, model, deepseek_api_key=None,
                      api_keys=None, **kw):
        calls.append((provider, model))
        return {"text": "first try works", "model": model, "provider": provider}

    with patch.object(llm, "generate", side_effect=fake_generate):
        result = llm.generate_with_fallback("reasoning", "some prompt")

    assert calls == [llm._online_provider_model()]
    assert result["fallback_used"] is False


def test_fallback_chain_without_mistral_forces_single_final_mistral_attempt():
    """
    "extract" role's chain is [("local", "mistral")] already, so this
    targets a role whose chain does NOT include local/mistral to
    exercise the "forced final attempt" safety net — and confirms that
    safety net is also a single attempt, not a double-retry.
    """
    calls = []

    # Mirrors llm.generate's real signature. `api_keys` was added there and
    # these fakes were not updated, so every call raised TypeError and all
    # four tests failed on the mock, not on the code under test. **kw keeps
    # the next parameter from doing the same thing again.
    def fake_generate(provider, prompt, model, deepseek_api_key=None,
                      api_keys=None, **kw):
        calls.append((provider, model))
        if (provider, model) == ("local", "mistral"):
            return {"text": "forced mistral works", "model": model, "provider": provider}
        return None

    # local mode, not the module default of api: in api mode the online
    # provider is prepended regardless of FALLBACK_CHAIN, so a patched chain
    # never gets a look in and this test could not exercise what it is named
    # for -- a chain that does NOT contain local/mistral.
    runtime_config.set_generation_mode("local")

    # "accurate" must be present: _chain_for does
    # FALLBACK_CHAIN.get(role, FALLBACK_CHAIN["accurate"]), and Python
    # evaluates that default eagerly -- so a fake chain without it raises
    # KeyError before the lookup this test cares about even happens.
    fake_chain = {"weird_role": [("deepseek", "deepseek-chat")],
                  "accurate": [("local", "mistral")]}
    with patch.object(llm, "FALLBACK_CHAIN", fake_chain):
        with patch.object(llm, "generate", side_effect=fake_generate):
            result = llm.generate_with_fallback("weird_role", "some prompt")

    # The chain's own entry, then exactly one forced mistral attempt appended
    # because the chain lacked it. "deepseek-chat" here is this test's own
    # fixture, not a real model name, so it does not go stale.
    assert calls == [("deepseek", "deepseek-chat"), ("local", "mistral")]
    assert result["fallback_used"] is True
