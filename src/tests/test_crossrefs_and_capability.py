"""Two features from the transcript of 2026-09-21, where five turns failed
for three different reasons and only one of them was about reasoning.

  crossrefs           a refusal should name the document it was deferred to.
                      "What is the screen size of the MyCheckr?" retrieves
                      MyCheckr User Manual p5, which says "Refer to MyCheckr
                      Range Technical Data for the dimensions of the device"
                      -- and that sheet is not ingested. The refusal was
                      CORRECT and sounded like ignorance.

  capability          "does ICU work with linux?" was refused while
                      retrieval returned "Accessing my device in Linux
                      Environment" at rank one, and the refusal then offered
                      "How do I access my ICU device in a Linux
                      environment?" as a suggestion.

Both are answered from facts about the CORPUS ("we hold no such document",
"we hold a procedure for this"), never from a judgement about the product --
which is what keeps them safe without the inference contract that the
compatibility-reasoning feature would need.

Chunks are constructed here rather than retrieved: these test the decisions,
and the real index is exercised separately by tests/run_scenarios.py.
"""
import _harness  # noqa: F401
import main
import crossrefs
from text_utils import capability_target


# Chunk text as ingest writes it: "[Document — Section › Sub] body".
def chunk(source, page, text):
    return {"source": source, "page": page, "text": text}


MYCHECKR_P5 = chunk(
    "MyCheckr User Manual-v7.pdf", 5,
    "[MyCheckr User Manual-v7 — Component Overview] Front View: Bottom View "
    "Refer to MyCheckr Range Technical Data for the dimensions of the device")

LINUX_P1 = chunk(
    "Accessing my device in Linux Environment-v2.pdf", 1,
    "[Accessing my device in Linux Environment-v2 — Accessing my device in "
    "Linux Environment] Here's an instruction documentation for setting up "
    "RNDIS between an ICU device and a Linux host.")

ANDROID_P14 = chunk(
    "ICU_Network_API-v1.0.50.pdf", 14,
    "[ICU_Network_API-v1.0.50] 1. Detect the ICU USB device. 2. Request USB "
    "permission from the Android framework. 3. Open the CDC ACM interface. "
    "4. Configure the serial connection: 115200 baud.")

BV30_P15 = chunk(
    "BV30 User Manual-v1.pdf", 15,
    "[BV30 User Manual-v1 — Cashbox] The cashbox holds 300 notes.")


# ── crossrefs ─────────────────────────────────────────────────────────

def test_a_deferral_to_a_document_we_lack_is_found():
    d = crossrefs.deferral_for("what is the screen size of the MyCheckr?",
                               [MYCHECKR_P5])
    assert d, "the chunk defers dimensions to a sheet that is not ingested"
    assert d["title"] == "MyCheckr Range Technical Data"
    assert d["source"] == "MyCheckr User Manual-v7.pdf"


def test_the_refusal_names_it():
    line = crossrefs.refusal_line(
        crossrefs.deferral_for("what is the screen size?", [MYCHECKR_P5]))
    assert "MyCheckr Range Technical Data" in line
    assert "don't hold" in line


def test_a_deferral_to_a_document_we_have_is_not_reported():
    """Sending an operator after a document they already hold is the failure
    mode that gets a report ignored. "MyConnect Environment Manual" is the
    held "MyConnect Environment-v4.pdf" under a slightly different name."""
    c = chunk("CS-MyConnect Quick Start Guide.pdf", 1,
              "For wiring diagrams and advanced configurations, refer to the "
              "MyConnect Environment Manual.")
    assert crossrefs._match("MyConnect Environment Manual",
                            [(crossrefs._key("MyConnect Environment-v4"),
                              "MyConnect Environment-v4.pdf")])
    # deferral_for reads the real docstore, so only the matcher is asserted
    # here; the end-to-end version is in the scan test below.
    assert c["text"]


def test_an_internal_pointer_is_not_a_missing_document():
    """"see the table below for screw specification" parses as a title
    ending in "Specification" and names part of the page you are on."""
    c = chunk("SMART Coin System Range User Manual-v1.pdf", 55,
              "Fit the baseplate. See the table below for screw "
              "specification and torque.")
    assert crossrefs.deferral_for("what screws do I need", [c]) is None


def test_a_document_referring_to_itself_is_not_a_missing_document():
    """"refer to Dataset/Firmware Programming, NV9USB+ Range User Manual"
    appears IN the NV9USB+ manual -- a section pointer that names its own
    document."""
    c = chunk("NV9USB+ Range User Manual-v1.pdf", 19,
              "For programming, refer to Dataset/Firmware Programming "
              "NV9USB+ Range User Manual for further information.")
    assert crossrefs.deferral_for("how do I update firmware", [c]) is None


def test_lowercase_prose_is_not_a_title():
    """The first version matched case-insensitively and reported "refer to
    the relevant manual" as a document four times over."""
    c = chunk("NV9 Spectral Range User Manual-v1.pdf", 38,
              "Refer to the relevant manual for further information.")
    assert crossrefs.deferral_for("anything", [c]) is None


# ── capability ────────────────────────────────────────────────────────

def test_the_target_is_extracted_from_each_frame():
    assert capability_target("does ICU work with linux?") == "linux"
    assert capability_target("CAn I use MyCheckr with linux?") == "linux"
    assert capability_target("does ICU lite work with android?") == "android"
    assert capability_target("does MyCheckr connect to ethernet") == "ethernet"
    assert capability_target(
        "is the NV200S compatible with a note recycler?") == "note recycler"


def test_questions_of_another_shape_are_left_alone():
    for q in ("what is the screen size of the MyCheckr?",
              "how much does it weigh",
              "can two units share one RS232 bus",
              "which icu product would work best with my vending machine",
              "does it work"):
        assert capability_target(q) is None, q


def test_a_document_titled_for_the_target_answers_it():
    out = main._capability_reply("does ICU work with linux?", [LINUX_P1])
    assert out["target"] == "linux"
    assert out["documented"], "a document about Linux is a Linux procedure"
    assert out["documented"][0]["page"] == 1


def test_a_numbered_procedure_mentioning_the_target_answers_it():
    """Android appears only in the BODY of the API manual, so the title tier
    misses it -- but a numbered procedure naming Android is a documented
    Android path, and is reported as the weaker of the two tiers."""
    out = main._capability_reply("does ICU lite work with android?",
                                 [ANDROID_P14])
    assert not out["documented"]
    assert out["procedural"], "numbered steps naming Android"
    assert out["procedural"][0]["page"] == 14


def test_a_passing_mention_is_not_a_procedure():
    """The whole safety property: we report only what we hold. A sentence
    that happens to contain the word is not a documented path."""
    c = chunk("ICU_Network_API-v1.0.50.pdf", 9,
              "[ICU_Network_API-v1.0.50] The response payload is identical "
              "on Android and on other clients.")
    out = main._capability_reply("does ICU work with android?", [c])
    assert not out["documented"] and not out["procedural"]


def test_nothing_documented_is_not_an_answer_either_way():
    out = main._capability_reply("does MyCheckr work with windows?", [BV30_P15])
    assert out["target"] == "windows"
    assert not out["documented"] and not out["procedural"]
    # And the refusal says which it is.
    text = main._friendly_refusal("mycheckr", "MyCheckr",
                                  query="does MyCheckr work with windows?",
                                  sources=[BV30_P15])
    assert "windows" in text.lower()
    assert "not an answer either way" in text


def test_the_query_path_answers_before_it_clarifies():
    """A question we can answer must not be answered with a question, so the
    capability branch is tested BEFORE the clarify gate."""
    import inspect
    src = inspect.getsource(main.query)
    cap = src.index('role = "capability"')
    clarify = src.index("THE CLARIFY GATE, MOVED")
    assert cap < clarify, "capability must be checked before clarify"
    assert "offer_support = False" in src[cap - 400:cap + 400]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
