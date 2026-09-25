#!/usr/bin/env python3
"""Score the ten customer conversations in tests/scenarios/ and report a
success percentage per scenario and overall.

    cd src && ../.venv/Scripts/python.exe tests/run_scenarios.py
    ... --verbose      show every turn, not only the failures
    ... --before       also score the pre-2026-09-19 code, for the delta

WHAT THIS DOES AND DOES NOT PROVE. No language model is involved, and none
is available (the gateway is NXDOMAIN). This scores the ROUTING decisions
the recent fixes changed -- whether a turn is treated as a follow-up, and so
whether a refusal asks a question back or stops dead; whether a price
question reaches the operator's deflect instead of the model; which buttons
a clarify turn offers; and whether an unscoped refusal suggests the right
product's questions. It says nothing about answer quality. eval.py is the
instrument for that, and it needs a provider.

Ground truth in the scenario files is what a support engineer reading the
transcript would say, written before the first run. Where the code disagrees
the turn is scored as a failure and printed -- the labels are not tuned to
make the number look better.
"""
import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import sales                      # noqa: E402
import text_utils as T            # noqa: E402

SCENARIO_DIR = HERE / "scenarios"

# Any non-empty history: the classifiers only need a conversation to exist.
# Turn 1 of each scenario is scored with an EMPTY history, because a first
# message has nothing to follow.
PRIOR = [{"q": "earlier question", "a": "earlier answer"}]


def product_context(q, product):
    """What main.py appends to the query before retrieval. Feeding this to
    the follow-up decision is the bug fixed on 2026-09-19; `--before`
    reproduces it."""
    return f"{q} ({product})" if product else q


# The classifier as it stood on 2026-09-17, frozen here so the baseline
# stays reproducible after text_utils stopped having these internals to
# poke at. A copy, deliberately: the point of a baseline is that it does
# not move when the thing being measured does.
_LEGACY_PATTERNS = [
    re.compile(r"^(more|elaborate|continue|further)\b", re.I),
    re.compile(r"\btell me more\b", re.I),
    re.compile(r"\b(step \d+|from step|from above|the above|as above"
               r"|as mentioned|from that)\b", re.I),
    re.compile(r"^(and |also |but )\b", re.I),
    re.compile(r"^(and |so |ok(ay)? )?(what|how) about\b", re.I),
    re.compile(r"\b(give me that|show me that|what about that"
               r"|more about (that|it)|more context|more detail)\b", re.I),
    re.compile(r"^\s*(it|they|them|those|these)\b", re.I),
    re.compile(r"\b(it|its|they|them|those|these|that one)\b", re.I),
    re.compile(r"\bi need more context\b", re.I),
    re.compile(r"\b(both|the two|the pair|either of them|each of them)\b", re.I),
]
_LEGACY_SHORT_ONLY = {6}      # the off-by-one, as shipped
_LEGACY_MAX_WORDS = 8


def _legacy_has_markers(q):
    n = len(q.split())
    for i, p in enumerate(_LEGACY_PATTERNS):
        if i in _LEGACY_SHORT_ONLY and n > _LEGACY_MAX_WORDS:
            continue
        if p.search(q):
            return True
    return False


def classify(q, history, product, legacy=False):
    if not legacy:
        # condensed_query == q: the conversation did not rewrite this turn.
        return T.is_followup_turn(q, history, q)
    if not history:
        return False
    # is_followup_turn's own shape, with the product-context query it was
    # wrongly being handed.
    return (_legacy_has_markers(q)
            or product_context(q, product) != q)


def score_scenario(sc, legacy=False, verbose=False):
    passed = failed = 0
    lines = []
    product = sc.get("product")

    for i, turn in enumerate(sc["turns"]):
        history = [] if i == 0 else PRIOR
        q = turn["q"]

        checks = []
        got = classify(q, history, product, legacy)
        checks.append(("follow-up", turn["followup"], got))

        # The commercial deflect is not affected by the follow-up work, but a
        # price question answered from a manual is the worst failure this
        # system has, so every scenario carries some.
        checks.append(("commercial", turn["commercial"],
                       sales.is_commercial_question(q)))

        if "markers" in turn:
            checks.append(("markers", turn["markers"],
                           T.has_reference_markers(q)))

        bad = [(name, want, got) for name, want, got in checks if want != got]
        if bad:
            failed += 1
            for name, want, got in bad:
                lines.append(f"      FAIL {name}: expected {want}, got {got}")
                lines.append(f"           \"{q}\"")
                lines.append(f"           ({turn['note']})")
        else:
            passed += 1
            if verbose:
                lines.append(f"      ok   \"{q[:66]}\"")

    # ── scenario-level checks ────────────────────────────────────────────
    extra_pass = extra_fail = 0

    sources = sc.get("expect_labels_sources") or (
        [sc["source"]] if sc.get("source") else [])
    if sources:
        got_labels = T.build_clarification_options(
            "ambiguous_in_domain", PRIOR, [{"source": s} for s in sources])
        want_labels = sc.get("expect_labels", [])
        if got_labels == want_labels:
            extra_pass += 1
            if verbose:
                lines.append(f"      ok   clarify buttons: {got_labels or '(none)'}")
        else:
            extra_fail += 1
            lines.append(f"      FAIL clarify buttons: expected {want_labels}, "
                         f"got {got_labels}")
            if sc.get("expect_labels_note"):
                lines.append(f"           ({sc['expect_labels_note']})")

    if sc.get("source") and sc.get("product_key"):
        # An unscoped refusal mid-conversation must not offer another
        # product's questions -- the logs.jsonl:1969 failure.
        suggestions = refusal_suggestions(
            sc["turns"][-1]["q"], sc["source"], legacy=legacy)
        others = [s for s in suggestions
                  if not tagged_to(s, sc["product_key"])]
        if others:
            extra_fail += 1
            lines.append("      FAIL refusal suggestions come from another "
                         "product's curated set:")
            for s in others:
                lines.append(f"           - {s}  [tagged: {tags_for(s)}]")
        else:
            extra_pass += 1
            if verbose:
                lines.append("      ok   refusal suggestions stay on product:")
                for s in suggestions:
                    lines.append(f"           - {s}")

    return passed + extra_pass, failed + extra_fail, lines


def _entry_for(question):
    import faq_store
    for e in faq_store.list_for_product(None):
        if (e.get("question") or "").strip() == question.strip():
            return e
    return None


def tags_for(question):
    e = _entry_for(question)
    return (e or {}).get("products") or (e or {}).get("category") or "?"


def tagged_to(question, product_key):
    """Is this suggestion tagged to the product in hand?

    Checked on the FAQ entry's PRODUCT TAG, not on the words in it. The
    first version of this check read the question text and flagged
    "Can MyCheckr devices use Wi-Fi instead of Ethernet?" as off-product
    for MyConnect -- but that entry is correctly tagged to MyConnect, which
    is the thing that manages MyCheckr devices. Reading the words would
    have failed the scoping fix for doing its job.
    """
    e = _entry_for(question)
    if e is None:
        return True          # not in the store: nothing to judge it against
    tags = [t.strip().lower().replace(" ", "_").replace("-", "_")
            for t in (e.get("products") or "").split(",") if t.strip()]
    cat = (e.get("category") or "").strip().lower()
    want = (product_key or "").lower()
    return want in tags or want == cat or not tags


_refusal_fn = None


def refusal_suggestions(query, source, legacy=False):
    """main._refusal_suggestions, lifted out of main.py.

    main.py pulls in the whole retrieval stack at import; this function does
    not need any of it. `legacy` reproduces the pre-fix behaviour: the whole
    displayable set, in file order.
    """
    global _refusal_fn
    if legacy:
        import faq_store
        return [e["question"] for e in faq_store.list_for_product(
            None, display_only=True) if (e.get("answer") or "").strip()][:3]
    if _refusal_fn is None:
        src = (HERE.parent / "main.py").read_text(encoding="utf-8")
        body = src[src.index("def _refusal_suggestions"):
                   src.index("def _friendly_refusal")]
        ns = {"logger": type("L", (), {"debug": staticmethod(lambda *a: None),
                                       "warning": staticmethod(lambda *a: None)})()}
        exec(body, ns)
        _refusal_fn = ns["_refusal_suggestions"]
    return _refusal_fn(None, query=query, sources=[{"source": source}])


def run(legacy=False, verbose=False):
    files = sorted(SCENARIO_DIR.glob("*.json"))
    if not files:
        sys.exit(f"no scenarios in {SCENARIO_DIR}")

    total_pass = total_fail = 0
    rows = []
    for f in files:
        sc = json.loads(f.read_text(encoding="utf-8"))
        p, fl, lines = score_scenario(sc, legacy=legacy, verbose=verbose)
        total_pass += p
        total_fail += fl
        pct = 100.0 * p / (p + fl) if (p + fl) else 0.0
        rows.append((sc["id"], sc["product"], p, fl, pct, lines))

    label = "BEFORE (code as of 2026-09-17)" if legacy else "AFTER (current)"
    print(f"\n{'=' * 72}\n  {label}\n{'=' * 72}")
    for sid, product, p, fl, pct, lines in rows:
        flag = "" if fl == 0 else "   <<"
        print(f"\n  {sid}")
        print(f"     {product}")
        print(f"     {p}/{p + fl} checks  {pct:5.1f}%{flag}")
        for line in lines:
            print(line)

    total = total_pass + total_fail
    pct = 100.0 * total_pass / total if total else 0.0
    print(f"\n{'-' * 72}")
    print(f"  {len(rows)} scenarios, {total} checks: "
          f"{total_pass} passed, {total_fail} failed")
    print(f"  SUCCESS: {pct:.1f}%")
    print(f"{'-' * 72}\n")
    return pct, total_pass, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true",
                    help="print every turn, not only failures")
    ap.add_argument("--before", action="store_true",
                    help="also score the pre-2026-09-19 code")
    args = ap.parse_args()

    if args.before:
        old_pct, old_p, old_t = run(legacy=True, verbose=args.verbose)
    new_pct, new_p, new_t = run(legacy=False, verbose=args.verbose)

    if args.before:
        print(f"  before {old_pct:.1f}%  ->  after {new_pct:.1f}%   "
              f"({new_p - old_p:+d} checks)\n")
    return 0 if new_p == new_t else 1


if __name__ == "__main__":
    sys.exit(main())
