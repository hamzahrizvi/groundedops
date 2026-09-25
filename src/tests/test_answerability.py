"""The answerability decision -- one classification, five outcomes.

Before this, four features each sniffed the question independently at
four points in main.query(): crossrefs and the capability scan inside
_friendly_refusal, the capability scan AGAIN in query(), sales ~4000
lines earlier, and the clarify gate deciding without reference to any of
them. These tests pin the ORDER they now resolve in, because the order is
the whole design -- each outcome is checked before the ones that claim
more, and UNANSWERABLE (today's refusal) is the default anything
uncertain falls to.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import answerability as A  # noqa: E402


def chunk(source, page, text):
    return {"source": source, "page": page, "text": text}


LINUX = chunk(
    "Accessing my device in Linux Environment-v2.pdf", 1,
    "[Accessing my device in Linux Environment-v2 — Accessing my device in "
    "Linux Environment] Here's an instruction documentation for setting up "
    "RNDIS between an ICU device and a Linux host.")

ANDROID = chunk(
    "ICU_Network_API-v1.0.50.pdf", 14,
    "[ICU_Network_API-v1.0.50] 1. Detect the ICU USB device. 2. Request USB "
    "permission from the Android framework. 3. Open the CDC ACM interface. "
    "4. Configure the serial connection: 115200 baud.")

DEFERRAL = chunk(
    "MyCheckr User Manual-v7.pdf", 5,
    "[MyCheckr User Manual-v7 — Component Overview] Front View: Bottom View "
    "Refer to MyCheckr Range Technical Data for the dimensions of the device")

BV30 = chunk("BV30 User Manual-v1.pdf", 15,
             "[BV30 User Manual-v1 — Cashbox] The cashbox holds 300 notes.")


# ── the outcomes ─────────────────────────────────────────────────────

def test_a_turn_that_answered_is_stated_and_is_not_re_opened():
    """This module exists to tell the failures apart from each other, not
    to second-guess the successes."""
    d = A.classify("what does the cashbox hold?", [BV30], refused=False)
    assert d["kind"] == A.STATED


def test_a_documented_procedure_is_stated():
    d = A.classify("does ICU work with linux?", [LINUX], refused=True)
    assert d["kind"] == A.STATED
    assert d["target"] == "linux"
    assert d["capability"]["documented"]


def test_a_numbered_procedure_in_the_body_is_also_stated():
    """The second tier: no heading says Android, but a numbered procedure
    naming it is still a documented Android path."""
    d = A.classify("does ICU work with android?", [ANDROID], refused=True)
    assert d["kind"] == A.STATED
    assert d["capability"]["procedural"]


def test_a_deferral_to_a_document_we_lack_is_documented_elsewhere():
    d = A.classify("what is the screen size of the MyCheckr?", [DEFERRAL],
                   refused=True)
    assert d["kind"] == A.DOCUMENTED_ELSEWHERE
    assert "Technical Data" in d["deferral"]["title"]


def test_a_compatibility_question_we_document_nothing_for_is_inferable():
    d = A.classify("does the BV30 work with windows?", [BV30], refused=True)
    assert d["kind"] == A.INFERABLE
    assert d["target"] == "windows"


def test_a_recommendation_request_is_advisory():
    d = A.classify("what product would work best with my vending machine?",
                   [BV30], refused=True)
    assert d["kind"] == A.ADVISORY


def test_anything_else_is_unanswerable():
    d = A.classify("what is the capital of France?", [BV30], refused=True)
    assert d["kind"] == A.UNANSWERABLE


# ── the order, which is the design ───────────────────────────────────

def test_naming_the_missing_document_beats_reasoning_about_it():
    """A concrete next step -- "that sheet exists and we don't hold it" --
    is worth more to a visitor than an inference, so DOCUMENTED_ELSEWHERE
    is checked before INFERABLE even when both would fire."""
    q = "does the MyCheckr work with a 7 inch screen?"
    d = A.classify(q, [DEFERRAL], refused=True)
    assert d["kind"] == A.DOCUMENTED_ELSEWHERE


def test_a_compatibility_question_with_no_context_is_not_inferable():
    """With nothing retrieved there are no premises, so there is nothing
    to reason FROM and an inference would be invention wearing a hedge."""
    d = A.classify("does the BV30 work with windows?", [], refused=True)
    assert d["kind"] != A.INFERABLE


def test_a_spec_question_wearing_a_commercial_shape_is_not_advisory():
    """"which products run on 24V" IS answerable from the documents, and
    test_commercial_questions pins that. The advisory test requires the
    asker's own situation ("my", "our"), which is the part no document
    can settle."""
    for q in ("which products run on 24V",
              "what is the best resolution supported"):
        assert A.classify(q, [BV30], refused=True)["kind"] != A.ADVISORY


# ── the one implementation ───────────────────────────────────────────

def test_main_keeps_the_old_name_as_an_alias_not_a_copy():
    """_capability_reply moved here. main still answers to that name --
    _friendly_refusal and query() both call it -- but there must be only
    one corpus scan, or the two will drift."""
    import inspect
    import main
    src = inspect.getsource(main._capability_reply)
    assert "answerability.capability_evidence" in src
    assert "documented, procedural, nearest = [], [], []" not in src, \
        "the scan was copied into main rather than moved out of it"
    assert (main._capability_reply("does ICU work with linux?", [LINUX])
            == A.capability_evidence("does ICU work with linux?", [LINUX]))


# ── the tagging tier, when ranking cannot reach the document ─────────

def test_a_document_tagged_to_the_product_answers_even_if_unranked():
    """"Can I use MyCheckr with linux?" -- the corpus holds "Accessing my
    device in Linux Environment", which the operator tagged to MyCheckr at
    upload. The document never says "MyCheckr", so the cross-encoder
    correctly scores it off-topic against a question naming MyCheckr and
    drops it from the reranked eight. Measured: it reaches the candidate
    pool and does not survive the rerank cut.

    Forcing candidates past that cut is the experiment this repo already
    ran and rejected (two regressions, mean grounding 0.993 -> 0.954), so
    this tier reads the tagging instead and touches neither ranking nor
    context.
    """
    import retrieval_db
    retrieval_db.sources_titled_for = lambda words, product: (
        ["Accessing my device in Linux Environment-v2.pdf"]
        if product in ("mycheckr", "mycheckr_mini") else [])
    d = A.classify("Can I use MyCheckr with linux?", [BV30],
                   refused=True, product="mycheckr")
    assert d["kind"] == A.STATED
    assert "Linux" in d["capability"]["documented"][0]["source"]


def test_another_products_question_does_not_reach_that_document():
    """THE SAFETY OF THE TIER. Unscoped, the same lookup would find the
    same document for "does the BV30 work with linux?" and claim a BV30
    Linux procedure that does not exist."""
    import retrieval_db
    retrieval_db.sources_titled_for = lambda words, product: (
        ["Accessing my device in Linux Environment-v2.pdf"]
        if product in ("mycheckr", "mycheckr_mini") else [])
    d = A.classify("does the BV30 work with linux?", [BV30],
                   refused=True, product="bv30")
    assert d["kind"] != A.STATED


def test_the_tagging_tier_is_last_not_first():
    """A document actually retrieved for THIS question is better evidence
    than a filename match, so the chunk scan wins when it finds anything.
    The tier is only consulted when the chunks showed nothing."""
    import retrieval_db
    called = []
    retrieval_db.sources_titled_for = lambda words, product: (
        called.append(product) or [])
    A.classify("does ICU work with linux?", [LINUX], refused=True,
               product="mycheckr")
    assert called == [], "the chunk scan already found it"


def test_no_product_scope_means_no_tagging_lookup():
    """Unscoped, there is no product to check the tagging against, and
    claiming a match on the filename alone is how the BV30 case above
    would go wrong."""
    import retrieval_db
    called = []
    retrieval_db.sources_titled_for = lambda words, product: (
        called.append(product) or [])
    A.classify("does the BV30 work with linux?", [BV30], refused=True)
    assert called == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
