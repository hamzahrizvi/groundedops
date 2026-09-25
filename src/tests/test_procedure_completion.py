"""The steps a retrieved passage points at but does not contain.

From the widget transcript of 2026-09-21, "how to get RNDIS working with
linux?". The corpus holds the answer -- eight chunks, Steps 1-4 carrying
the commands. Retrieval returned the sentence ANNOUNCING the procedure
and its Important Notes, and none of the steps; not ranked low, ranked
outside the top sixteen. The model was handed a pointer to a procedure
and its footnotes and said it could not find the answer, which on that
context was the correct thing to say.

A step reads "sudo touch /etc/udev/rules.d/80-local.rules". It contains
no word a person would type, so BM25 has nothing to match and its
embedding is nothing like a question's. The parts of a document that TALK
ABOUT a task out-retrieve the parts that DO it, on both rankings at once.
Widening the candidate window cannot fix that.

The collection is faked here: this pins the DECISIONS (what counts as an
introduction, what happens when a PDF's outline is inconsistent, document
order, the budget), none of which are properties of the index. Firing
rate against the real corpus is measured by
tests/measure_procedure_completion.py -- 2 of 37 questions.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import retrieval_db  # noqa: E402

DOC = "Accessing my device in Linux Environment-v2.pdf"

# The Linux document's real shape, including the inconsistency that
# matters: Steps 1-2 are nested under the introduction and Steps 3-4 sit
# at the top level, because of how the headings happened to be styled.
CHUNKS = [
    ("_0", "Customer Support - Accessing my device in Linux Environment", ""),
    ("_1", "Here's an instruction documentation for setting up RNDIS "
           "between an ICU device and a local Ethernet adapter:",
     "Accessing my device in Linux Environment"),
    ("_2", "1. Open a terminal. 2. sudo touch /etc/udev/rules.d/80-local.rules",
     "Accessing my device in Linux Environment › Step 1: Create a new udev rule file"),
    ("_3", "1. sudo touch /usr/bin/icuif.sh 2. Copy the bash script",
     "Accessing my device in Linux Environment › Step 2: Create the script"),
    ("_5", "1. sudo reboot OR sudo systemctl restart udev",
     "Step 3: Reboot or restart udev"),
    ("_6", "run ip addr show to verify the ICU Lite interface is available",
     "Step 4: Verify the ICU Lite interface"),
    ("_7", "The ICU Lite interface's IP address is static at 192.168.137.8",
     "Important Notes"),
]


class FakeCollection:
    def __init__(self, rows=CHUNKS, source=DOC):
        self.rows, self.source = rows, source
        self.calls = 0

    def get(self, where=None, **kw):
        self.calls += 1
        if (where or {}).get("source") != self.source:
            return {"ids": [], "documents": [], "metadatas": []}
        return {
            "ids": [self.source + i for i, _, _ in self.rows],
            "documents": [t for _, t, _ in self.rows],
            "metadatas": [{"source": self.source, "page": 1, "section": s}
                          for _, _, s in self.rows],
        }


def use(rows=CHUNKS):
    fake = FakeCollection(rows)
    retrieval_db.get_collection = lambda: fake
    return fake


def retrieved(*sections):
    return [{"id": DOC + "_x", "text": "...", "source": DOC, "page": 1,
             "section": s, "retrieval_score": 0.03} for s in sections]


def sections_of(chunks):
    return [c["section"] for c in chunks if c.get("fetched_by")]


# ── the case this exists for ─────────────────────────────────────────

def test_the_introduction_brings_the_steps_it_announces():
    use()
    out = retrieval_db.complete_procedures(
        retrieved("Accessing my device in Linux Environment"))
    got = sections_of(out)
    assert len(got) == 4, got
    assert all("Step" in s for s in got)


def test_steps_arrive_in_document_order():
    """A procedure delivered out of order is worse than none: the reader
    follows it and the machine ends up in a state the next step does not
    expect."""
    use()
    out = retrieval_db.complete_procedures(
        retrieved("Accessing my device in Linux Environment"))
    got = sections_of(out)
    assert [s.split("Step ")[1][0] for s in got] == ["1", "2", "3", "4"]


def test_a_flattened_outline_still_yields_the_whole_procedure():
    """Steps 1-2 are children of the introduction and Steps 3-4 are not,
    purely because of PDF heading styling. Taking only the children
    fetches half a procedure, which strands the reader mid-way."""
    use()
    got = sections_of(retrieval_db.complete_procedures(
        retrieved("Accessing my device in Linux Environment")))
    assert any(s.startswith("Step 3") for s in got)
    assert any(s.startswith("Step 4") for s in got)


# ── what must NOT trigger it ─────────────────────────────────────────

def test_a_chunk_that_introduces_nothing_fetches_nothing():
    """Only a chunk whose own section is the parent of a step heading is
    an introduction. Otherwise every chunk of a procedural document would
    drag in the whole document on every question."""
    use()
    assert sections_of(retrieval_db.complete_procedures(
        retrieved("Important Notes"))) == []


def test_a_step_already_retrieved_is_not_added_twice():
    use()
    out = retrieval_db.complete_procedures(retrieved(
        "Accessing my device in Linux Environment",
        "Accessing my device in Linux Environment › Step 1: Create a new udev rule file"))
    got = sections_of(out)
    assert not any(s.endswith("Step 1: Create a new udev rule file")
                   for s in got)
    assert len(got) == 3


def test_a_numbered_body_is_not_a_procedure():
    """Body numbering is how every parts list and pinout is written. The
    step marker has to be in the HEADING, or a pinout would fetch its
    whole document."""
    use([("_0", "1. Vend 1 2. Vend 2 3. Vend 5", "Connector pinout"),
         ("_1", "1. Red 2. Black", "Connector pinout › Wire colours")])
    assert sections_of(retrieval_db.complete_procedures(
        retrieved("Connector pinout"))) == []


def test_the_budget_is_respected():
    use()
    out = retrieval_db.complete_procedures(
        retrieved("Accessing my device in Linux Environment"), budget=80)
    total = sum(len(c["text"]) for c in out if c.get("fetched_by"))
    assert total <= 80


def test_fetched_passages_are_marked_and_ranked_last():
    """A fetched passage did not earn a position -- it is here because a
    retrieved one pointed at it, and the answering prompt treats passage
    order as a hint about which is most likely to answer."""
    use()
    out = retrieval_db.complete_procedures(
        retrieved("Accessing my device in Linux Environment"))
    assert all(c.get("fetched_by") == "procedure_completion"
               for c in out[1:])
    assert all(c["retrieval_score"] == 0.0 for c in out[1:])


# ── it must never cost an answer ─────────────────────────────────────

def test_a_broken_collection_degrades_to_todays_behaviour():
    """This runs on every query. A fault here has to return what it was
    given, not raise into the request path."""
    def boom():
        raise RuntimeError("chroma unavailable")
    retrieval_db.get_collection = boom
    given = retrieved("Accessing my device in Linux Environment")
    assert retrieval_db.complete_procedures(given) == given


def test_empty_input_is_returned_untouched():
    use()
    assert retrieval_db.complete_procedures([]) == []


if __name__ == "__main__":
    _real = retrieval_db.get_collection
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            finally:
                retrieval_db.get_collection = _real
    print("ALL CHECKS PASSED")
