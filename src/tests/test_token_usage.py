"""Token accounting from each provider's usage block.

Worth its own tests because the per-session token cap in quota.py is only
as real as this function: llm.py used to discard provider usage entirely,
which would have made "tokens per conversation" a setting that silently
did nothing. Deliberately NOT using _harness — that stubs llm out, and the
real module is the thing under test here.
"""
from llm import _usage_tokens


def test_openai_shape():
    assert _usage_tokens({"usage": {"total_tokens": 123}}, "openai") == 123


def test_deepseek_uses_openai_shape():
    assert _usage_tokens({"usage": {"total_tokens": 40}}, "deepseek") == 40


def test_anthropic_sums_input_and_output():
    body = {"usage": {"input_tokens": 10, "output_tokens": 5}}
    assert _usage_tokens(body, "anthropic") == 15


def test_ollama_reads_its_own_counters():
    # Ollama reports these at the top level, not under "usage".
    assert _usage_tokens({"prompt_eval_count": 7, "eval_count": 3}, "local") == 10


def test_missing_usage_is_zero_not_an_error():
    # 0 means "not reported". Callers must treat it as unknown, never as
    # free — a provider that stops reporting usage should not silently
    # become uncapped.
    assert _usage_tokens({}, "openai") == 0
    assert _usage_tokens({"usage": {}}, "anthropic") == 0


def test_garbage_usage_does_not_raise():
    # A provider returning a string where a number belongs must not take
    # down the answer that has already been generated and paid for.
    assert _usage_tokens({"usage": {"total_tokens": "lots"}}, "openai") == 0
    assert _usage_tokens({"usage": None}, "openai") == 0
    assert _usage_tokens({}, "unknown-provider") == 0
