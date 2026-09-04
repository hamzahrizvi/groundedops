"""The follow-up offer after an answer, and when it must NOT be made.

The load-bearing assertion is the negative one. "More detail" is only worth
a button if the material behind it says something the answer did not; an
offer that restates the answer spends the visitor's click and teaches them
the button lies. Every other mode is a fallback from that judgement.
"""
import more_context


ANSWER = ("The NV9USB+ operates from 0 to 50 degrees Celsius at 5 to 95 "
          "percent relative humidity, non-condensing.")


def _chunk(cid, text, source="NV9USB+ Range User Manual-v1.pdf", page=30,
           rerank_score=0.9):
    """rerank_score defaults to clearly-relevant. A surplus chunk now has to
    clear the cross-encoder's own decision boundary before its novelty is
    even considered, so a fixture without one is not a realistic chunk."""
    return {"id": cid, "text": text, "source": source, "page": page,
            "rerank_score": rerank_score}


USED = [_chunk("u1", "Operating temperature 0 to 50 C. Humidity 5 to 95 "
                     "percent relative, non-condensing.")]

SOURCES = [{"source": "NV9USB+ Range User Manual-v1.pdf",
            "download_url": "/source_file/NV9USB%2B%20Range%20User%20Manual-v1.pdf",
            "pages": [30]}]


# ── novelty, which everything else rests on ─────────────────────────────

def test_a_restatement_is_not_novel():
    frac, _ = more_context.novelty(
        "The operating temperature is 0 to 50 degrees Celsius.", ANSWER)
    assert frac < more_context.MIN_NOVELTY, frac


def test_genuinely_new_material_is_novel():
    frac, count = more_context.novelty(
        "Storage temperature is -20 to 70 C. Vibration resistance conforms "
        "to IEC 60068-2-6. Ingress protection rating IP54 when bezel fitted.",
        ANSWER)
    assert frac >= more_context.MIN_NOVELTY, frac
    assert count >= more_context.MIN_NEW_WORDS


def test_empty_text_is_not_novel():
    assert more_context.novelty("", ANSWER) == (0.0, 0)


# ── mode: detail ────────────────────────────────────────────────────────

def test_offers_detail_when_unused_chunks_add_something():
    extra = _chunk("x1", "Storage temperature -20 to 70 C. Vibration per IEC "
                         "60068-2-6. Ingress protection IP54 with bezel.")
    got = more_context.build(USED + [extra], USED, ANSWER, SOURCES)
    assert got["kind"] == "detail", got
    assert got["chunk_ids"] == ["x1"]
    assert got["passages"][0]["page"] == 30
    # The document pointer rides along in every mode -- "here is more, and
    # the original is at page 30" beats either half alone.
    assert got["document"]["pages"] == [30]


def test_does_not_offer_detail_that_merely_restates_the_answer():
    dupe = _chunk("x2", "Operating temperature range 0 to 50 degrees Celsius.")
    got = more_context.build(USED + [dupe], USED, ANSWER, SOURCES)
    assert got["kind"] == "document", got
    assert got["chunk_ids"] == []


def test_ignores_surplus_from_a_document_the_answer_did_not_use():
    """A high-ranking chunk from another manual is a different subject, not
    more context on this answer."""
    other = _chunk("o1", "The SMART Coin System sorts and dispenses coins at "
                         "twelve coins per second with a 1200 coin hopper.",
                   source="SMART Coin System Range User Manual-v1.pdf", page=8)
    got = more_context.build(USED + [other], USED, ANSWER, SOURCES)
    assert got["kind"] == "document", got
    assert got["chunk_ids"] == []


def test_detail_is_capped():
    extras = [_chunk(f"x{i}",
                     f"Distinct specification {i}: vibration IEC{i} ingress "
                     f"IP5{i} altitude {i}000 metres shock {i}0g acoustic "
                     f"{i}0 decibels enclosure variant {i}")
              for i in range(8)]
    got = more_context.build(USED + extras, USED, ANSWER, SOURCES)
    assert len(got["chunk_ids"]) <= more_context.MAX_DETAIL_CHUNKS


# ── mode: document, then support ────────────────────────────────────────

def test_falls_back_to_the_document_with_its_page():
    got = more_context.build(USED, USED, ANSWER, SOURCES)
    assert got["kind"] == "document"
    assert got["document"]["download_url"].startswith("/source_file/")
    assert "page 30" in got["label"]


def test_falls_back_to_support_when_there_is_no_document():
    got = more_context.build([], [], ANSWER, [])
    assert got["kind"] == "support"
    assert got["support"] is True
    assert "support" in got["label"].lower()


def test_a_refusal_never_claims_there_is_more_detail():
    """Nothing cleared the retrieval gate, so there is no honest 'more' --
    and this is the path most in need of offering a person."""
    extra = _chunk("x1", "Storage temperature -20 to 70 C. Vibration per IEC "
                         "60068-2-6. Ingress protection IP54 with bezel.")
    got = more_context.build([extra], [], "I could not find that in the "
                                          "documentation.", [], refused=True)
    assert got["chunk_ids"] == []
    assert got["support"] is True


def test_multiple_pages_are_listed():
    src = [dict(SOURCES[0], pages=[12, 14, 30])]
    got = more_context.build(USED, USED, ANSWER, src)
    assert "pages 12, 14, 30" in got["label"], got["label"]


def test_an_irrelevant_novel_chunk_is_not_offered():
    """The defect that relevance-first exists to fix. Asked how to install
    and mount an NV9USB+, a novelty-only rule proposed "Protocols and
    Interfacing", "SMART Update currencies" and "Cleaning the Product" --
    maximally novel precisely BECAUSE they are about something else."""
    offtopic = _chunk(
        "z1", "Cleaning the Product. Do not use solvent based cleaners such "
              "as alcohol or petroleum spirit on the bezel or lens surface.",
        rerank_score=0.32)
    got = more_context.build(USED + [offtopic], USED, ANSWER, SOURCES)
    assert got["chunk_ids"] == [], got
    assert got["kind"] == "document"


def test_detail_is_ordered_by_relevance_not_novelty():
    """Ranking by novelty puts the most off-topic passage first by
    construction, which is the opposite of useful."""
    near = _chunk("near", "Bezel mounting uses four M4 screws at 45mm "
                          "centres with a torque of 1.2 Nm maximum.",
                  rerank_score=0.94)
    far = _chunk("far", "Acoustic emission measured 62 decibels while "
                        "dispensing, altitude limit 2000 metres, shock "
                        "rating 30g, ingress IP54 enclosure variant B.",
                 rerank_score=0.55)
    got = more_context.build(USED + [far, near], USED, ANSWER, SOURCES)
    assert got["kind"] == "detail"
    assert got["chunk_ids"][0] == "near", got["chunk_ids"]
