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

    # In api mode the online provider is the whole chain: the forced
    # local/mistral attempt is a LOCAL-mode safety net and no longer runs
    # here (it was a 240s connect to an Ollama that api-mode hosts do not
    # have). What this guards is unchanged: each model is tried EXACTLY
    # once, never twice (the old safe_generate behaviour that let one model
    # burn the whole time budget).
    #
    # The expected entry is derived, not literal -- the online model comes
    # from ONLINE_DEEPSEEK_MODEL, and a hardcoded "deepseek-chat" here broke
    # the moment that retired alias was replaced.
    assert calls == [llm._online_provider_model()]
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

# ── which key does which job (v16.5) ──────────────────────────────────
# A second saved key used to change nothing: every role went to the one
# provider named in Settings. These pin what an assignment actually does to
# the chain, which is the only place the feature is observable.
#
# `_rewrite_env_line` is patched out throughout: keystore.set_role writes to
# a real .env, and this module runs without _harness's ENV_FILE_PATH
# redirect, so an unpatched run would edit the developer's own file.

from contextlib import contextmanager

import keystore


@contextmanager
def roles(assignments, keyed=True):
    """Assignments live in os.environ only, with every provider reporting a
    key (or none of them, for `keyed=False`)."""
    with patch.object(keystore, "_rewrite_env_line", lambda k, v: None), \
         patch.object(keystore, "has_key", lambda p: keyed):
        for role in keystore.roles():
            keystore.set_role(role, assignments.get(role))
        try:
            yield
        finally:
            for role in keystore.roles():
                keystore.set_role(role, None)


def test_default_assignment_leads_the_chain_and_advanced_falls_back_to_it():
    with roles({"default": "anthropic"}):
        assert llm._chain_for("accurate")[0][0] == "anthropic"
        # An unassigned Advanced must not mean "nothing" — a deep question
        # still has to be answered.
        assert llm._provider_for_job("advanced") == "anthropic"
        assert llm._chain_for("reasoning")[0][0] == "anthropic"


def test_advanced_assignment_routes_only_the_reasoning_role():
    with roles({"default": "anthropic", "advanced": "deepseek"}):
        assert llm._chain_for("reasoning")[0][0] == "deepseek"
        assert llm._chain_for("accurate")[0][0] == "anthropic"
        assert llm._chain_for("fast")[0][0] == "anthropic"


def test_no_backup_leaves_the_chain_one_attempt_long():
    with roles({"default": "anthropic"}):
        assert len(llm._chain_for("accurate")) == 1


def test_backup_appends_one_attempt_after_the_leader():
    with roles({"default": "anthropic", "backup": "deepseek"}):
        assert [p for p, _ in llm._chain_for("accurate")] == ["anthropic", "deepseek"]


def test_backup_equal_to_the_leader_is_not_two_attempts_at_one_provider():
    # Retrying the model that just failed is exactly the behaviour this
    # module exists to prevent.
    with roles({"default": "anthropic", "backup": "anthropic"}):
        assert len(llm._chain_for("accurate")) == 1


def test_an_assignment_whose_key_was_removed_does_not_route_generation():
    with roles({"default": "anthropic"}, keyed=False):
        # The assignment is still written — masked, not lost — but a provider
        # certain to fail auth must not lead the chain.
        assert keystore.get_role_assignment("default") == "anthropic"
        assert keystore.get_role("default") is None
        assert llm._chain_for("accurate")[0][0] == runtime_config.get_online_provider()


def test_local_mode_ignores_key_roles_entirely():
    with roles({"default": "anthropic", "backup": "deepseek"}):
        runtime_config.set_generation_mode("local")
        try:
            assert llm._chain_for("accurate") == llm.FALLBACK_CHAIN["accurate"]
        finally:
            runtime_config.set_generation_mode("api")


def test_deepseek_calls_disable_thinking_unless_asked(monkeypatch=None):
    """DeepSeek V4 thinks by default and bills the thought. Every DeepSeek
    request -- answer and stream alike -- must say so explicitly, and the
    env switch must be able to turn it back on."""
    import os
    import llm

    seen = []

    class _Res:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}],
                    "usage": {"total_tokens": 3}}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def iter_lines(self, decode_unicode=True):
            return iter(["data: [DONE]"])

    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        seen.append(json)
        return _Res()

    real_post = llm._HTTP.post
    llm._HTTP.post = fake_post
    saved = os.environ.pop("DEEPSEEK_THINKING", None)
    try:
        llm._call_deepseek("q", model="deepseek-v4-flash", api_key="k")
        list(llm.stream_generate("deepseek", "q", "deepseek-v4-flash",
                                 api_keys={"deepseek": "k"}))
        assert [j["thinking"] for j in seen] == [{"type": "disabled"}] * 2
        # An OpenAI-compatible gateway is not sent a DeepSeek field.
        list(llm.stream_generate("openai", "q", "itl-gpt-flash",
                                 api_keys={"openai": "k"}))
        assert "thinking" not in seen[-1]
        os.environ["DEEPSEEK_THINKING"] = "on"
        llm._call_deepseek("q", model="deepseek-v4-flash", api_key="k")
        assert seen[-1]["thinking"] == {"type": "enabled"}
    finally:
        llm._HTTP.post = real_post
        os.environ.pop("DEEPSEEK_THINKING", None)
        if saved is not None:
            os.environ["DEEPSEEK_THINKING"] = saved


def test_a_provider_in_cooldown_is_skipped_when_the_chain_has_another():
    """The gateway that answered 5xx a moment ago is not asked again on the
    very next question -- unless it is the only provider there is."""
    import runtime_config, keystore, llm

    calls = []

    def fake_generate(provider, prompt, model, deepseek_api_key=None, api_keys=None):
        calls.append(provider)
        return {"text": "ok", "model": model, "provider": provider}

    real_generate, real_chain = llm.generate, llm._chain_for
    llm.generate = fake_generate
    llm._provider_down.clear()
    try:
        llm._chain_for = lambda role: [("openai", "itl-gpt-pro"), ("deepseek", "deepseek-v4-flash")]
        llm._note_unreachable("openai")
        out = llm.generate_with_fallback("reasoning", "q")
        assert calls == ["deepseek"] and out["provider"] == "deepseek"
        # The only entry is always tried, cooldown or not.
        calls.clear()
        llm._chain_for = lambda role: [("openai", "itl-gpt-pro")]
        llm.generate_with_fallback("reasoning", "q")
        assert calls == ["openai"]
        # A success clears it.
        llm._note_reachable("openai")
        assert not llm.provider_cooling("openai")
    finally:
        llm.generate, llm._chain_for = real_generate, real_chain
        llm._provider_down.clear()


def test_api_mode_never_forces_a_local_attempt():
    """In api mode a total online failure ends the chain; it does not fall
    through to a 240s Ollama connect on a host that has no Ollama."""
    import runtime_config, llm

    calls = []

    def fake_generate(provider, prompt, model, deepseek_api_key=None, api_keys=None):
        calls.append((provider, model))
        return None

    real_generate, real_chain = llm.generate, llm._chain_for
    saved_mode = runtime_config.get_generation_mode()
    llm.generate = fake_generate
    llm._provider_down.clear()
    try:
        runtime_config.set_generation_mode("api")
        llm._chain_for = lambda role: [("deepseek", "deepseek-v4-flash")]
        out = llm.generate_with_fallback("accurate", "q")
        assert calls == [("deepseek", "deepseek-v4-flash")]
        assert out["provider"] == "none"
        runtime_config.set_generation_mode("local")
        calls.clear()
        llm.generate_with_fallback("accurate", "q")
        assert ("local", "mistral") in calls
    finally:
        llm.generate, llm._chain_for = real_generate, real_chain
        runtime_config.set_generation_mode(saved_mode)
        llm._provider_down.clear()
