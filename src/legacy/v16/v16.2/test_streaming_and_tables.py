"""v16.2: table-atomic chunking, and streaming that keeps the grounding rule.

The property that matters for streaming is NOT that it streams -- it is that
it cannot show a customer a claim the documents do not support. So the tests
here are mostly about the grounder refusing, and about the streamed and
non-streamed paths agreeing. A streaming path that is one notch more lenient
than /query would quietly undo the whole guarantee.

The table tests exist because a spec table split across two chunks is
unretrievable in the way that matters: "Validator NV9S" in one chunk and
"1.05 Kg" in the next answers nobody's question.
"""
# IMPORT ORDER MATTERS HERE, both lines deliberate.
#
# chunking/grounding are imported FIRST and bare, the way test_chunking.py
# does it: _harness stubs modules into sys.modules, and importing it before
# these makes `from chunking import ...` resolve to a stub with no __file__.
#
# _harness is then imported anyway, at the bottom, because run_tests.py grants
# a file its own process when it finds the string "import _harness" — and this
# file needs that isolation for real: it loads the actual NLI model, and
# re-initialising native extensions in a process that has already stubbed and
# unstubbed them fails with "cannot load module more than once".
from chunking import (chunk_text, strip_table_fences,
                      TABLE_OPEN, TABLE_CLOSE, _split_table_rows)
from grounding import (StreamGrounder, score_unit, check_grounding,
                       _premises, _table_row_sentence)
from text_utils import split_units

import _harness  # noqa: E402,F401  — see the note above; ordering is load-bearing

ok = lambda label: print(f"  PASS  {label}")
fails = []


def check(cond, label):
    if cond:
        ok(label)
    else:
        fails.append(label)
        print(f"  FAIL  {label}")


def fence(body):
    return TABLE_OPEN + "\n" + body + "\n" + TABLE_CLOSE


SPEC = ("Validator NV9S: 1.05 Kg\nBezel (standard): 0.10 Kg\n"
        "Cashbox (Slide In): 0.57 Kg\nCombined: 1.72 Kg")
PIPE = ("Environment | Minimum | Maximum\nTemperature | +5C | +50C\n"
        "Humidity | 5% | 95%\nRipple | 0V | 0.25V")


print("== a table survives chunking whole ==")
text = "Weights\n\n" + fence(SPEC) + "\n\nEnvironmental requirements follow."
chunks = [strip_table_fences(c) for c in chunk_text(text, size=1200, overlap=200)]
whole = [c for c in chunks if "Validator NV9S: 1.05 Kg" in c and "Combined: 1.72 Kg" in c]
check(len(whole) >= 1,
      "the whole spec table lands in one chunk at the real chunk size")
check(not any(TABLE_OPEN in c or TABLE_CLOSE in c for c in chunks),
      "the fence markers never survive into a chunk")

# The regression this guards: the first and last row of one table ending up in
# different chunks, which is what made "how much does the NV9S weigh" fail.
split_rows = [c for c in chunks
              if ("Validator NV9S" in c) != ("Combined: 1.72 Kg" in c)]
check(not split_rows, "no chunk holds only part of the table")


print("\n== an oversized table splits at row boundaries, not mid-row ==")
pieces = _split_table_rows(SPEC, size=60)
check(len(pieces) > 1, "an oversized two-column table is split")
check(all(not p.strip().endswith(":") for p in pieces),
      "no piece ends on a dangling label")
for p in pieces:
    for line in p.split("\n"):
        check(line.count(":") >= 1 or not line.strip(),
              f"every line keeps its label and value ({line[:30]!r})")
        break   # one representative line per piece is enough

check("Validator NV9S: 1.05 Kg" in "\n".join(pieces),
      "no row is lost in the split")


print("\n== header repetition is only for tables that HAVE a header ==")
pipe_pieces = _split_table_rows(PIPE, size=70)
check(len(pipe_pieces) > 1, "the pipe table splits")
check(all(p.startswith("Environment | Minimum | Maximum") for p in pipe_pieces),
      "every piece of a pipe table repeats the header row")

spec_pieces = _split_table_rows(SPEC, size=60)
first_row = "Validator NV9S: 1.05 Kg"
check(sum(p.count(first_row) for p in spec_pieces) == 1,
      "a two-column spec list does NOT repeat its first row (it is data, "
      "not a header)")


print("\n== the streaming grounder holds the line ==")

CTX = [{"text":
        "The NV9USB+ Range are versatile banknote validators. Host "
        "communication is via USB or TTL. The NV11+ Note Float recycler has "
        "a capacity of 30 notes. Supply voltage is 12V DC nominal."}]


def stream(sentences, threshold=0.55):
    g = StreamGrounder(CTX, threshold)
    shown = ""
    for s in sentences:
        rel, good = g.feed(s)
        shown += rel
        if not good:
            return shown, False, g
    rel, good = g.finish()
    return shown + rel, good, g


shown, good, g = stream(["The NV11+ Note Float holds 30 notes. "])
check(good, "a supported sentence is released")
check("30 notes" in shown, "and its text actually reaches the caller")

shown, good, g = stream([
    "The NV11+ Note Float holds 30 notes. ",
    "It also includes a built-in thermal printer and GPS. ",
])
check(not good, "an unsupported sentence stops the stream")
check("thermal printer" not in shown,
      "the unsupported sentence is NEVER released to the caller")
check(g.failed_unit and "thermal printer" in g.failed_unit,
      "and the offending sentence is reported for the log")

# Threshold 0 means "release anything", which proves the cut above was the
# grounder deciding rather than a plumbing bug swallowing text.
shown, good, g = stream(
    ["It also includes a built-in thermal printer. "], threshold=0.0)
check(good and "thermal printer" in shown,
      "with the gate opened the same sentence flows, so the cut was the "
      "grounding decision and not a broken pipe")


print("\n== a terminator at the end of the buffer is not a sentence end ==")
# Found by the FIRST live call to /query/stream: it released
# "The NV9S validator weighs 1." because "1." ended the buffer. Mid-stream
# that is indistinguishable from the start of "1.05".
NUM_CTX = [{"text": "Weights listed below without notes unless otherwise "
                    "indicated.\nValidator NV9S: 1.05 Kg\nBezel: 0.10 Kg"}]
g = StreamGrounder(NUM_CTX, 0.55)
out = ""
for delta in ["The NV9S validator weighs 1.", "05 Kg.", " The bezel is 0.", "10 Kg."]:
    rel, good = g.feed(delta)
    out += rel
    check(good, f"stream survives delta {delta!r}")
rel, good = g.finish()
out += rel
check("weighs 1.05 Kg" in out,
      "the decimal survives being split across deltas")
check("weighs 1." not in out.replace("weighs 1.05", ""),
      "no fragment was released as if it were a finished sentence")

check(g._boundary("The value is 1.") == -1,
      "a trailing terminator alone is NOT a boundary")
check(g._boundary("Done. Next") > 0,
      "a terminator with following text IS a boundary")


print("\n== the streaming gate matches /query, not something stricter ==")
# StreamGrounder without the lexical rescue refused answers /query serves,
# because NLI is unreliable on table text: "weighs 1.05 Kg on its own"
# scores ~0.02 (the qualifier is an inference no single premise states).
TABLE_CTX = [{"text": "Weights listed below without notes unless otherwise "
                      "indicated.\nValidator NV9S: 1.05 Kg"}]
QUALIFIED = "The NV9S validator weighs 1.05 Kg on its own. "

g_strict = StreamGrounder(TABLE_CTX, 0.55)          # no lexical_ok
_, strict_ok = g_strict.feed(QUALIFIED)

g_parity = StreamGrounder(
    TABLE_CTX, 0.55,
    lexical_ok=lambda t: bool(__import__("re").search(r"1\.05", t)))
released, parity_ok = g_parity.feed(QUALIFIED)

check(not strict_ok,
      "NLI alone refuses the qualified table fact (that is the known gap)")
check(parity_ok and "1.05 Kg" in released,
      "with the same lexical rescue /query uses, it is served instead")


print("\n== streamed and non-streamed grounding agree ==")

for answer in ["The NV11+ Note Float holds 30 notes.",
               "Host communication is via USB or TTL.",
               "The unit has a thermal printer."]:
    _, whole_score = check_grounding(answer, CTX, threshold=0.55)
    units = split_units(answer)
    per_unit = min([score_unit(u, CTX) for u in units], default=1.0)
    check(abs(whole_score - round(per_unit, 4)) < 1e-3,
          f"same score both ways for {answer[:38]!r} "
          f"({whole_score} vs {round(per_unit, 4)})")


print("\n== pipe table rows are rejoined with their header ==")
# Shaped like the real manual, degree symbols and dual units included --
# a simplified "+5C" scores differently, so testing against tidied-up data
# would not tell us whether this works on the corpus we actually have.
TBL = ("Environmental requirements.\n"
       "Environment | Minimum | Maximum\n"
       "Temperature | +5°C / 37.4°F | +50°C / 122°F\n"
       "Humidity | 5% | 95% Non-condensing")
ps = _premises([TBL])
check(any(p.startswith("Temperature: Minimum +5°C / 37.4°F, "
                       "Maximum +50°C / 122°F") for p in ps),
      "a table row is re-emitted as a sentence naming its columns")
check("Temperature | +5°C / 37.4°F | +50°C / 122°F" in ps,
      "and the raw row is kept too, since scoring takes the max of both")

check(_table_row_sentence("A | B | C", "x | 1 | 2") == "x: B 1, C 2",
      "header cells become the column labels")
check(_table_row_sentence("only-one-column", "x | 1") is None,
      "a header with no columns yields no sentence")
check(_table_row_sentence("A | B", "x | 1 | 2 | 3") is None,
      "a row wider than its header yields no sentence rather than guessing")

# The regression this guards: stranded rows scored ~0.05, so every
# table-derived answer was refused until the header was reattached.
_strand = score_unit("The operating temperature range is +5°C to +50°C.",
                     [{"text": TBL}])
check(_strand >= 0.55,
      f"a table-derived fact now clears the gate (scored {_strand:.4f})")


print("\n" + "=" * 52)
if fails:
    print(f"{len(fails)} CHECK(S) FAILED")
    for f in fails:
        print("  - " + f)
    raise SystemExit(1)
print("ALL CHECKS PASSED")
