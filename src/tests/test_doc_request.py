"""doc_request: a request for a document itself is answered with its link;
a question ABOUT a document's content is left to the pipeline."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doc_request import document_request, pick_documents, reply  # noqa: E402

SOURCES = ["MyCheckr User Manual-v7.pdf",
           "Accessing my device in Linux Environment-v2.pdf",
           "CS-MyCheckr Installation & MyConnect App Pre-Requisites Checklist.pdf"]


def test_requests_for_the_document_are_recognised():
    for q, kind in [
        ("can you give me MyCheckr manual?", "manual"),
        ("Can you send me the MyCheckr user manual please", "manual"),
        ("give me the manual", "manual"),
        ("I need the NV9USB+ datasheet", "datasheet"),
        ("where can I download the user guide for the MyCheckr", "manual"),
        ("do you have a data sheet for the BV30?", "datasheet"),
        ("could I have a copy of the installation guide", "guide"),
        ("hi, can u send the MyCheckr documentation", None),
        ("link to the pdf", None),
    ]:
        got = document_request(q)
        assert got is not None, q
        assert got["kind"] == kind, (q, got)


def test_questions_about_a_documents_content_are_not():
    for q in [
        "what does the manual say about the pinout?",
        "give me the steps from the manual for a factory reset",
        "can you give me the reset procedure in the manual",
        "how do I reset the MyCheckr",
        "does ICU work with linux",
        "is there a section in the manual on cleaning?",
        "which page of the manual covers the LEDs",
        "can you give me the pinout for the nv9",
        "I need to reset the password",
    ]:
        assert document_request(q) is None, q


def test_the_kind_asked_for_is_preferred():
    docs, matched = pick_documents("manual", SOURCES)
    assert docs == ["MyCheckr User Manual-v7.pdf"] and matched


def test_no_kind_offers_everything_held():
    docs, matched = pick_documents(None, SOURCES)
    assert len(docs) == 3 and matched


def test_a_missing_kind_offers_what_is_held_without_passing_it_off():
    docs, matched = pick_documents("datasheet", SOURCES)
    assert len(docs) == 3 and not matched
    text = reply("MyCheckr", "datasheet", docs, matched)
    assert text.startswith("I don't hold a data sheet for the MyCheckr")


def test_nothing_held_offers_nothing():
    assert pick_documents("manual", [])[0] == []


def test_single_document_reply_names_it():
    text = reply("MyCheckr", "manual", ["MyCheckr User Manual-v7.pdf"], True)
    assert text == ("Here is the MyCheckr User Manual-v7 — use the Download "
                    "link below to get it.")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
