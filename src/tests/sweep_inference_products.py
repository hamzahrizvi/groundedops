"""Hallucination sweep: the SAME compatibility question shape across every
product in the corpus, against REAL retrieved chunks.

No model is reachable, so this does not generate the answers. For each
product it writes the answer a model would plausibly produce, in the exact
shape build_inference_prompt asks for, and asks whether contract 2 serves
it. Two per product, differing in ONE clause:

    sound     recombines only what the product's own retrieved passage says
    invented  same sentence, hedged and attributed identically, naming a
              component/standard/feature the passages never mention

If an invented one is served, the contract is not doing its job. If no
sound one is served, the contract is a refuse-everything gate wearing a
costume -- the failure the first two probes of it actually had.

    cd src && ../.venv/Scripts/python.exe tests/sweep_inference_products.py
    ... --show      print the premise each case uses, and nothing else

Needs the real index and the real NLI model, so it is a script rather
than part of run_tests.py -- the same arrangement as sweep_grounding.py.
tests/test_inference_contract.py pins the RULES with a faked scorer;
this pins that those rules still separate sound from invented once the
real model and the real retrieval are in the loop. It is also where the
two faults that mattered were found, neither of which a hand-built chunk
could have shown: the vocabulary lock pooling another product's passage,
and the contradiction check being vetoed by page footers.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grounding
import answerability
from retrieval_db import retrieve_from_db

SHOW = "--show" in sys.argv

# question, filename hint for the product's OWN passage, sound clause,
# invented clause. The sound clause is written from the premise --show
# prints, so it recombines words that passage contains and nothing else.
CASES = [
    ("does the NV200S work with a windows host", "NV200S",
     "a host system with an IF17 cable should be able to have communication",
     "a Windows PC should work over the bundled USB HID driver"),
    ("does the BV30 work with polymer notes", "BV30",
     "the BV30 should be designed for stacker-less applications",
     "polymer notes should be accepted by the BV30"),
    ("does the NV9USB+ work with a raspberry pi", "NV9USB",
     "a fitting replacement product listed above should be pin to pin "
     "compatible",
     "a Raspberry Pi should work over the standard Linux CDC driver"),
    ("does the SMART Coin System work with a cashless reader", "SMART Coin",
     "a machine housing should need a re-design for the Twin SMART Coin "
     "System",
     "a cashless reader should be able to share the ccTalk bus"),
    ("does MyCheckr work with android", "MyCheckr",
     "a MyCheckr should function as a standalone device with only the power "
     "cable",
     "an Android tablet should pair over Bluetooth LE"),
    ("does the NV9 Spectral work with 24V", "NV9 Spectral",
     "a supply voltage should be within the minimum and maximum",
     "a 24V supply should work with the optional step-down regulator"),
    ("does MyConnect work with linux", "MyConnect",
     "a MyCheckr device should be able to have real-time communication with "
     "the MyConnect Hub",
     "a Linux host should work through the MyConnect CLI package"),
    ("does the NV200 Spectral work with ccTalk", "NV200",
     "the NV200 Spectral should connect to a ccTalk host machine using the "
     "PA02014 docking interface",
     "ccTalk should work once the ccTalk daughterboard is fitted"),
]

FRAME_A = "The documentation doesn't say."
FRAME_B = "That is my reading of the specification, not a stated claim."


def premise_from(chunk):
    """The first substantial sentence of a chunk, verbatim."""
    body = chunk["text"]
    body = body[body.index("]") + 1:] if "]" in body[:400] else body
    for s in body.replace("\n", " ").split("."):
        if len(s.strip()) > 40:
            return s.strip()
    return body[:200].strip()


rows, bad = [], 0
for q, hint, sound_c, invented_c in CASES:
    chunks = retrieve_from_db(q, top_k=5)
    if not chunks:
        print(f"  no chunks for {q!r} -- skipped")
        continue
    # The premise must come from the PRODUCT'S OWN passage. Taking
    # chunks[0] blindly took a MyConnect passage for the NV9 Spectral
    # question and then judged a conclusion written from the NV9 voltage
    # table against it -- measuring the sweep's own wiring, not the gate.
    own = next((c for c in chunks if hint.lower() in (c["source"] or "").lower()),
               chunks[0])
    prem = premise_from(own)
    if SHOW:
        print(f"\n== {q}\n   source:  {own['source']}\n   premise: {prem[:200]}")
        continue
    decision = answerability.classify(q, chunks, refused=True)
    for kind, clause, must_refuse in (("sound", sound_c, False),
                                      ("invented", invented_c, True)):
        ans = f"{FRAME_A} {prem}. So {clause}. {FRAME_B}"
        ok, rep = grounding.check_inference(ans, chunks)
        if must_refuse and ok:
            bad += 1
            verdict = "**SERVED AN INVENTION**"
        else:
            verdict = "served" if ok else "refused"
        rows.append((q, kind, decision["kind"], verdict, rep.get("reason", "")))

if SHOW:
    sys.exit(0)

print()
for q, kind, dkind, verdict, reason in rows:
    print(f"{verdict:24} [{kind:8}] switchboard={dkind:20} {q}")
    if reason:
        print(f"{'':24}  reason: {reason[:96]}")
print()
n_sound = sum(1 for r in rows if r[1] == "sound")
served_sound = sum(1 for r in rows if r[1] == "sound" and r[3] == "served")
print(f"invented answers served: {bad} of "
      f"{sum(1 for r in rows if r[1] == 'invented')}  (must be 0)")
print(f"sound answers served:    {served_sound} of {n_sound}")
sys.exit(1 if bad else 0)
