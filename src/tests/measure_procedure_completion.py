"""Blast radius of procedure completion.

recall@k cannot fall from an additive fetch, so eval_retrieval.py is the
wrong instrument. What matters is how OFTEN this fires and how much it
adds when it does: every fetched chunk is context the model must read and
a premise the grounding verifier will accept, so a change that fired on
most questions would be a quiet loosening of the gate.

    cd src && ../.venv/Scripts/python.exe tests/measure_procedure_completion.py

Needs the real index, so it is a script rather than part of
run_tests.py -- the same arrangement as sweep_grounding.py.
tests/test_procedure_completion.py pins the decisions with a faked
collection; this reports the blast radius against the real corpus.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrieval_db import retrieve_from_db, complete_procedures

cases = json.load(open("eval_cases.json", encoding="utf-8"))
qs = [c["q"] for c in (cases if isinstance(cases, list) else cases["cases"])]
qs += [
    "how to get RNDIS working with linux?",
    "how do I access my ICU device in a linux environment?",
    "does ICU lite work with andorid?",
]

fired, rows = 0, []
for q in qs:
    ch = retrieve_from_db(q, top_k=8)
    out = complete_procedures(ch)
    added = [c for c in out if c.get("fetched_by")]
    if added:
        fired += 1
        rows.append((q, len(added), sum(len(c["text"]) for c in added),
                     sorted({c["source"] for c in added})))

print(f"\nfired on {fired} of {len(qs)} questions "
      f"({100 * fired / len(qs):.0f}%)\n")
for q, n, chars, srcs in rows:
    print(f"  +{n} chunk(s) {chars:5}ch  {q[:56]}")
    for s in srcs:
        print(f"{'':22}  <- {s}")
