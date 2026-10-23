"""The rewriter runs on every turn with history; the prompt decides.

PENDING, "Condensation can destroy a working query, and is gated by the
wrong thing" -- the second fault. `has_reference_markers()` had to match or
condense_query returned the query untouched, so a follow-up whose wording
the marker list did not cover never got rewritten at all. That is what
killed "what is the power required to run both at once": no marker matched,
no rewrite ran, and a product-less fragment hit retrieval. `de5a37f` added
set-anaphora markers, which extends the list rather than fixing the design.

The gate was a second classifier doing a job already asked of the model:
CONDENSE_PROMPT_TEMPLATE ends "If the latest message is ALREADY a complete,
self-contained question ... return it EXACTLY AS-IS". Removing it is safe
only because retrieval now searches the raw phrasing alongside the rewritten
one (tests/test_query_fusion.py), so an unnecessary rewrite costs candidates
it must win on merit rather than the original's hits.

Deliberately does NOT import _harness: the harness stubs llm itself.
"""
import llm


class _Spy:
    def __init__(self, reply=None):
        self.prompts = []
        self.reply = reply

    def __call__(self, model, prompt, timeout=None, num_predict=None,
                 **kwargs):
        self.prompts.append(prompt)
        return {"text": self.reply} if self.reply else None


HIST = [{"q": "what voltage does the NV9 Spectral need", "a": "12 V DC."}]


def _run(monkeypatched_call, query, history=HIST):
    orig_call = llm._call_ollama
    orig_mode = None
    try:
        import runtime_config
        orig_mode = runtime_config.get_generation_mode
        runtime_config.get_generation_mode = lambda: "local"
    except Exception:
        pass
    llm._call_ollama = monkeypatched_call
    try:
        return llm.condense_query(query, history)
    finally:
        llm._call_ollama = orig_call
        if orig_mode is not None:
            import runtime_config
            runtime_config.get_generation_mode = orig_mode


def test_a_query_with_no_marker_still_reaches_the_model():
    """The regression this removes. "what is the power required to run both
    at once" matched nothing, so phi was never asked."""
    spy = _Spy(reply="what is the power required to run the NV9 Spectral "
                     "and the Note Float at once")
    out = _run(spy, "what is the power required to run both at once")

    assert spy.prompts, "the rewriter was skipped -- the regex gate is back"
    assert out == ("what is the power required to run the NV9 Spectral "
                   "and the Note Float at once")


def test_a_plainly_standalone_query_also_reaches_the_model():
    """Not an oversight. Judging self-containment is the model's job now,
    and the prompt asks for the query back unchanged -- which is what a
    caller relying on the old short-circuit would have got anyway."""
    q = "post installation verification installer sign off"
    spy = _Spy(reply=q)
    out = _run(spy, q)

    assert spy.prompts
    assert out == q


def test_the_prompt_carries_the_instruction_that_replaced_the_gate():
    """If this sentence ever leaves the template, the gate needs to come
    back -- nothing else would stop a standalone query being rewritten."""
    from text_utils import CONDENSE_PROMPT_TEMPLATE
    assert "EXACTLY AS-IS" in CONDENSE_PROMPT_TEMPLATE


def test_no_history_still_short_circuits():
    """The one guard that remains: with no prior turns there is nothing to
    resolve against, so the model call is pure cost."""
    spy = _Spy(reply="anything at all")
    out = _run(spy, "tell me more about that", history=[])

    assert not spy.prompts
    assert out == "tell me more about that"


def test_a_model_failure_leaves_the_query_alone():
    spy = _Spy(reply=None)
    out = _run(spy, "and the other one?")

    assert spy.prompts
    assert out == "and the other one?"
