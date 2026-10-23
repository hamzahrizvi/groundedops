#!/usr/bin/env python3
"""Score the ten customer conversations in tests/scenarios/ and report a
success percentage per scenario and overall.

    cd src && ../.venv/Scripts/python.exe tests/run_scenarios.py
    ... --verbose      show every turn, not only the failures
    ... --before       also score the pre-2026-09-19 code, for the delta
    ... --transcript   keyless replay: needs_history against the resolved
                       queries saved in
                       docs/stress-test-2026-09-25-transcripts-after.md,
                       instead of the raw question this file's classify()
                       otherwise resolves on its own (no condense_query call,
                       no provider key -- M10)

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

# A fixed catalogue, set before catalog is imported (it reads the path at
# import). The button labels in the scenarios are product NAMES, and
# catalog_config.json is gitignored operator data: CI fell back to the seed
# catalogue and scored 123/150 against a floor of 148. The fixture is the
# operator's catalogue of 2026-10-02 minus owner and digest fields.
import os                         # noqa: E402
os.environ.setdefault("CATALOG_CONFIG",
                      str(HERE / "fixtures" / "catalog_fixture.json"))

import sales                      # noqa: E402
import text_utils as T            # noqa: E402

SCENARIO_DIR = HERE / "scenarios"
TRANSCRIPT_DOC = HERE.parent.parent / "docs" / "stress-test-2026-09-25-transcripts-after.md"

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
        # M10: scenarios 11-24 carry `needs_history` (this same is_followup_
        # turn decision, renamed) instead of 01-10's bare `followup`, and it
        # may be null -- a deflect or an intent (main.py:1448 / :2501) reaches
        # some turns before routing ever asks whether they needed history, so
        # there is nothing honest to check there.
        if "needs_history" in turn:
            if turn["needs_history"] is not None:
                checks.append(("needs_history", turn["needs_history"], got))
        else:
            checks.append(("follow-up", turn["followup"], got))

        # The commercial deflect is not affected by the follow-up work, but a
        # price question answered from a manual is the worst failure this
        # system has, so every scenario carries some.
        checks.append(("commercial", turn["commercial"],
                       sales.is_commercial_question(q)))

        if "markers" in turn:
            checks.append(("markers", turn["markers"],
                           T.has_reference_markers(q)))

        # M10: intent is scored only against the two deterministic intent
        # classifiers, never invented for a shape neither one covers.
        if turn.get("intent") == "handoff":
            import intents
            checks.append(("intent", True, intents.is_handoff_request(q)))
        elif turn.get("intent") == "greeting":
            import intents
            checks.append(("intent", True, intents.is_greeting(q)))

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


def parse_transcript_turns(path):
    """{scenario_id: [(raw_q, resolved_q, answer), ...]} from a saved live
    transcript (tests/run_live.py's --out markdown), in turn order.

    A turn with no '- resolved query:' line was not rewritten by
    condensation, so its resolved text falls back to the raw question. The
    answer is carried too, because is_followup_turn's rewrite check
    (_rewrite_folded_in_history) looks for added words in the last two
    HISTORY turns' q+a -- feeding it a placeholder history (as
    score_scenario's fixed PRIOR does) can never agree with a real run,
    since the words a real rewrite pulled in came from this scenario's own
    prior answers, not from a generic stand-in.
    """
    text = path.read_text(encoding="utf-8")
    scenarios: dict[str, list] = {}
    sid = raw_q = resolved_q = None
    answer_lines: list[str] = []

    def _flush():
        if sid and raw_q is not None:
            scenarios.setdefault(sid, []).append(
                (raw_q, resolved_q or raw_q, " ".join(answer_lines).strip()))

    for line in text.splitlines():
        m = re.match(r"^## (\S+)", line)
        if m:
            _flush()
            sid, raw_q, resolved_q, answer_lines = m.group(1), None, None, []
            continue
        m = re.match(r"^### Turn (\d+): (.+)$", line)
        if m:
            _flush()
            raw_q, resolved_q, answer_lines = m.group(2).strip(), None, []
            continue
        m = re.match(r"^- resolved query: `(.*)`$", line)
        if m and sid and raw_q is not None:
            resolved_q = m.group(1)
            continue
        m = re.match(r"^> (.*)$", line)
        if m and sid and raw_q is not None:
            answer_lines.append(m.group(1))
    _flush()
    return scenarios


def run_transcript_replay(verbose=False):
    """Keyless: replays needs_history against the RESOLVED queries a real
    condense_query call already produced and this repo saved, with the real
    accumulated per-scenario history -- rather than calling condense_query
    again (which needs a provider key) or trusting classify()'s own
    approximation, which only ever sees the raw question against a fixed
    placeholder history."""
    scenarios = parse_transcript_turns(TRANSCRIPT_DOC)
    files = sorted(SCENARIO_DIR.glob("*.json"))
    total_pass = total_fail = 0
    for f in files:
        sc = json.loads(f.read_text(encoding="utf-8"))
        sid = sc["id"]
        transcript_turns = scenarios.get(sid)
        if not transcript_turns:
            continue
        history: list = []
        for i, turn in enumerate(sc["turns"], start=1):
            if i > len(transcript_turns):
                break
            raw_q, resolved_q, answer = transcript_turns[i - 1]
            want = turn.get("needs_history")
            if want is not None:
                got = T.is_followup_turn(raw_q, history, resolved_q)
                if got == want:
                    total_pass += 1
                else:
                    total_fail += 1
                    if verbose:
                        print(f"  FAIL {sid} T{i}: expected "
                              f"needs_history={want}, got {got}  "
                              f"resolved={resolved_q!r}")
            history.append({"q": raw_q, "a": answer})

    total = total_pass + total_fail
    pct = 100.0 * total_pass / total if total else 0.0
    print(f"\n--transcript replay: {total_pass}/{total} needs_history "
          f"checks agree with the saved resolved queries ({pct:.1f}%)")
    return 0 if total_fail == 0 else 1


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
    ap.add_argument("--transcript", action="store_true",
                    help="keyless: replay needs_history against the saved "
                         "resolved queries instead of the normal run")
    ap.add_argument("--min-pass", type=int, default=None,
                    help="M11/CI: fail if fewer than this many checks pass "
                         "(a regression gate looser than 'every check', "
                         "since a handful are known, documented misses -- "
                         "never raised by relabelling instead of fixing)")
    args = ap.parse_args()

    if args.transcript:
        return run_transcript_replay(verbose=args.verbose)

    if args.before:
        old_pct, old_p, old_t = run(legacy=True, verbose=args.verbose)
    new_pct, new_p, new_t = run(legacy=False, verbose=args.verbose)

    if args.before:
        print(f"  before {old_pct:.1f}%  ->  after {new_pct:.1f}%   "
              f"({new_p - old_p:+d} checks)\n")

    if args.min_pass is not None:
        if new_p < args.min_pass:
            print(f"  GATE: FAIL — {new_p} checks passed, below the "
                  f"committed floor of {args.min_pass}\n")
            return 1
        print(f"  GATE: PASS — {new_p} checks passed (floor {args.min_pass})\n")
        return 0
    return 0 if new_p == new_t else 1


if __name__ == "__main__":
    sys.exit(main())
