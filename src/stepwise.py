"""A two-product comparison answered in steps on the widget (v17.4 trial).

"Compare the NV9USB+ and the BV30 power supply" becomes three answers that
reach the visitor one by one: the NV9USB+ on its own, the BV30 on its own,
then the comparison. Each part is a full, grounded answer through
/widget/ask (gates and credits included). The comparison is written from
the two parts alone (compare()), one small model call, so it agrees with
them. It is not re-checked against the manuals; the facts it may use
already were. WIDGET_STEPWISE_COMPARE=0 turns the whole thing off.

Only where it pays (worth_it). The normal answer runs first and stands
unless it asked "which product?" or answered a named attribute from one
spec-table row. Measured 2026-11-07 on 13 comparisons: there the steps won
(the kiosk temperature, idle current, power supply); against a full
pipeline answer or an open "what's the difference" table they lost or tied,
for 3.7x the tokens and 5x the wait. When a part is missing or the call
fails, the normal answer stands.

plan() decides and words the per-product questions; widget_api's
/ask/stream runs them.
"""

import logging
import os
import re
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# True while a part runs. Parts skip the FAQ, as the comparison does (a
# single-product FAQ menu or near-match is not this question's answer), and
# main.py logs no "visitor rejected the FAQ" gap for them.
PART: ContextVar[bool] = ContextVar("stepwise_part", default=False)


def is_answer(r: dict) -> bool:
    """A part worth showing: an answer, not a question back (which product,
    which family member, which FAQ) or a sign-in wall. Pure."""
    return bool((r.get("answer") or "").strip()) and not any(
        r.get(k) for k in ("needs_clarification", "faq_candidates", "family_options",
                           "product_options", "needs_sign_in", "service_degraded",
                           "from_faq"))


def worth_it(q: str, r: dict, names: list[str]) -> bool:
    """Whether the steps should replace the normal answer `r` to comparison
    `q` naming `names`: it asked which product, or answered a named
    attribute from the spec table ("which draws less current when idle?"
    got the one "Maximum Current Sink" row). An open "what's the
    difference" table, a full answer, a FAQ menu or a sign-in wall stand.
    Pure.

    Only a model's answer: a FAQ-only turn (a guest at the site-wide cap,
    whose "pick one" also asks back) must not reach the model through the
    split and the comparison, uncharged."""
    if (r.get("needs_sign_in") or r.get("service_degraded") or r.get("flagged")
            or r.get("from_faq") or r.get("faq_candidates")
            or r.get("effort") == "faq_only"):
        return False
    if any(r.get(k) for k in ("needs_clarification", "product_options", "family_options")):
        return True
    if r.get("role") == "catalogue":
        import sales
        return bool(sales._asked(q, " ".join(names)))
    return False


SPLIT_PROMPT = """A customer asked a question comparing two products:

{q}

Rewrite it as two separate questions, one about each product, keeping exactly
what the customer wants to know. Both questions ask the SAME thing, just about
a different product: whatever is being compared applies to both, wherever it
appears in the sentence ("the difference between the A and the B power
supply" asks about the power supply of A and of B). Each question names only
its own product and never mentions other products.
Reply with exactly two lines and nothing else:
1. <question about {a}>
2. <question about {b}>"""


COMPARE_PROMPT = """A customer asked: {q}

Below is one answer per product, each already checked against that product's
manual.

### {a}
{answer_a}

### {b}
{answer_b}

Answer the customer's question by comparing the two products, using ONLY
facts stated above. Never add a figure, feature or claim that is not there,
and never draw a conclusion the facts do not state (a recommended supply
rating is not how much a product draws). Start with the direct answer to
exactly what was asked. A short markdown table helps when you compare
figures. If the facts above are not enough to decide, say plainly what is
missing. Keep it short and do not repeat everything above. Talk about the
products, not about "the answers"."""


def compare(q: str, parts: list[tuple[str, str]]) -> str | None:
    """The comparison written from the two parts [(name, answer)] alone, so
    it agrees with what the visitor has just read; None if no model answered
    (the caller then runs the normal pipeline)."""
    try:
        import llm
        (a, answer_a), (b, answer_b) = parts
        out = llm.generate_with_fallback("default", COMPARE_PROMPT.format(
            q=q, a=a, answer_a=answer_a, b=b, answer_b=answer_b))
        text = (out.get("text") or "").strip()
        return text if out.get("provider") != "none" and text else None
    except Exception as exc:
        logger.warning(f"stepwise: comparison not written ({exc})")
        return None


def record(session_id: str | None, q: str, answer: str, request_id: str,
           origin: str | None = None) -> None:
    """The comparison is the visitor's turn: remembered so a follow-up reads
    against it (in place of the normal answer it replaces), and logged like
    any answer, under the visitor's chat (so erasing the chat erases it) and
    who asked (so test chats are not counted as visitors)."""
    import memory
    import pipeline_trace as ptrace
    from logger import log_interaction
    if session_id:
        with memory._lock:
            h = memory._sessions.get(session_id)
            if h and h[-1].get("q") == q:
                h.pop()
        memory.add_to_memory(session_id, q, answer)
    tok = ptrace.start()
    try:
        ptrace.set_meta(surface="widget", session_id=session_id, origin=origin)
        log_interaction(q, answer, "comparison", None, [], request_id=request_id)
    finally:
        ptrace.reset(tok)


def part_session(session_id: str, i: int) -> str:
    """Part i's own chat id: the visitor's, marked, within the 128-character
    id cap (widget_api.MAX_ID_CHARS)."""
    return session_id[:125] + f"~{i}"


def with_parts(session_ids) -> list[str]:
    """These chat ids and their parts' ids, so erasing a chat erases the
    per-product questions and answers asked under it too."""
    return [x for s in session_ids if s
            for x in (s, part_session(s, 1), part_session(s, 2))]


def enabled() -> bool:
    return os.getenv("WIDGET_STEPWISE_COMPARE", "1") != "0"


def products(q: str) -> list[str]:
    """The two catalogue product keys a comparison question names, else []."""
    from main import _is_comparison, _product_names, _products_named_in
    if not _is_comparison(q):
        return []
    names = _product_names()
    named = list(dict.fromkeys(_products_named_in(q, list(names))))
    if len(named) != 2:
        return []
    # In the order the visitor named them; a product named only by an alias
    # goes last.
    low = q.lower()
    return sorted(named, key=lambda k: (low.find(names.get(k, k).lower()) % (len(low) + 1)))


_OTHERS = re.compile(r"\b(other|another)\s+(products?|models?|units?|devices?)\b", re.I)


def parse_split(text: str, keys: list[str], named) -> list[str] | None:
    """The two numbered lines of the model's reply, in `keys` order, each
    naming exactly one of the two products by any of its names (`named(line)`
    -> product keys; the replies came back in either order, and "NV200
    Spectral" for the NV200S); None if the reply is not that shape. Pure
    given `named`."""
    lines = [re.sub(r"^\s*\d[.)]\s*", "", ln).strip()
             for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) != 2 or any(_OTHERS.search(ln) for ln in lines):
        return None
    by_key = {}
    for line in lines:
        hit = named(line)
        if len(hit) != 1 or hit[0] not in keys or hit[0] in by_key:
            return None
        by_key[hit[0]] = line
    return [by_key[k] for k in keys]


def plan(q: str) -> list[tuple[str, str, str]]:
    """[(product key, display name, its own question)] x2 for a comparison of
    two products, else []. Any failure means [] and the normal single answer."""
    if not enabled():
        return []
    try:
        keys = products(q)
        if not keys:
            return []
        from main import _product_names, _products_named_in
        import llm
        names = [_product_names().get(k, k) for k in keys]
        out = llm.generate_with_fallback(
            "default", SPLIT_PROMPT.format(q=q, a=names[0], b=names[1]))
        if out.get("provider") == "none":
            return []
        lines = parse_split(out.get("text", ""), keys,
                            lambda line: _products_named_in(line, keys))
        # Products only: the question and its split are visitor text, which
        # the server log never prints (test_console_log_privacy).
        if not lines:
            logger.info(f"stepwise: split not usable for {keys}")
            return []
        logger.info(f"stepwise: split {keys}")
        return list(zip(keys, names, lines))
    except Exception as exc:
        logger.warning(f"stepwise: plan skipped ({exc})")
        return []


if __name__ == "__main__":
    forms = {"x100": "x100", "y200": "y200", "z300": "z300 pro"}
    named = lambda line: [k for k, f in forms.items() if f in line.lower()]  # noqa: E731
    k = ["x100", "y200"]
    assert parse_split("1. What supply does the X100 need?\n"
                       "2. What supply does the Y200 need?", k, named) == [
        "What supply does the X100 need?", "What supply does the Y200 need?"]
    # Either order; back in the order asked.
    assert parse_split("1. Y200 supply?\n2. X100 supply?", k, named) == [
        "X100 supply?", "Y200 supply?"]
    assert parse_split("1. X100 vs Y200 supply?\n2. Y200 supply?", k, named) is None
    assert parse_split("1. Y200 supply?\n2. Y200 size?", k, named) is None
    assert parse_split("Only one line about the X100", k, named) is None
    assert parse_split("1. X100 vs other products?\n2. Y200 vs other products?",
                       k, named) is None
    # Open "what's the difference" tables stand; a named attribute does not.
    cat = {"role": "catalogue", "answer": "| table |"}
    assert not worth_it("What is the difference between the X100 and the Y200?",
                        cat, ["X100", "Y200", "x100", "y200"])
    assert worth_it("X100 vs Y200 - which draws less current when idle?",
                    cat, ["X100", "Y200", "x100", "y200"])
    assert worth_it("x", {"role": "clarify", "needs_clarification": True}, [])
    assert not worth_it("x", {"role": "fast", "answer": "a"}, [])
    assert not worth_it("x", {"needs_clarification": True, "needs_sign_in": True}, [])
    # A capped guest's FAQ "pick one" asks back too, but never reaches a model.
    assert not worth_it("x", {"needs_clarification": True, "from_faq": False,
                              "effort": "faq_only"}, [])
    assert not is_answer({"answer": "Curated.", "from_faq": True})
    assert part_session("s" * 200, 2) == "s" * 125 + "~2"
    assert with_parts(["a", ""]) == ["a", "a~1", "a~2"]
    print("ok")
