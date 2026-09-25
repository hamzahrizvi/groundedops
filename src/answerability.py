"""The answerability decision: one classification, before the answer.

WHAT THIS REPLACES. The pipeline decided "did retrieval score well" and
nothing else, so every failure looked the same and got the same refusal.
Meanwhile four separate features had each grown their own sniff at the
question, at four different points in main.query():

    crossrefs.deferral_for      inside _friendly_refusal
    capability_target           inside _friendly_refusal AND in query()
    sales.is_sales_question     ~4000 lines earlier, before retrieval
    the clarify gate            in the refusal branch chain

That works for two features. At five it is a question sniffed five times
with five answers and no one place that says which one wins. This module
is that one place: it is asked ONCE, it returns ONE outcome, and the
branch chain in query() dispatches on it instead of re-deciding.

THE FOUR OUTCOMES, and what each is entitled to say:

    STATED                 the corpus states it. Extract and answer -- the
                           ordinary path, plus the capability case where
                           what is stated is that a PROCEDURE exists.
    DOCUMENTED_ELSEWHERE   a document we hold defers to one we do not.
                           Name the missing document (crossrefs).
    INFERABLE              the premises are documented and the conclusion
                           is not. Only reachable with the inference
                           contract on; refuses exactly as before when off.
    ADVISORY               not a documentation question at all. No manual
                           contains product-selection guidance, so no
                           corpus change fixes it.
    UNANSWERABLE           none of the above. Today's refusal.

ADVISORY is classified but NOT yet routed -- the advisory route needs
slot-typed pending state that conversation memory (a list of {q, a}
strings) cannot hold. Naming the outcome now is the point of a
switchboard: the classification is a fact about the question, and it is
true whether or not a route exists to act on it. It reaches the caller as
a label and nothing more.

NOTHING HERE CALLS A MODEL. Every outcome is decided from the question's
shape and from facts about the CORPUS -- what we hold, what a held
document points at. That is what keeps the classification safe to run on
every turn, and it is why a wrong classification costs a worse refusal
rather than a wrong answer.
"""
import logging
import re

logger = logging.getLogger(__name__)

STATED = "stated"
DOCUMENTED_ELSEWHERE = "documented_elsewhere"
INFERABLE = "inferable"
ADVISORY = "advisory"
UNANSWERABLE = "unanswerable"


def capability_evidence(query: str, chunks: list[dict],
                        product: str | None = None) -> dict | None:
    """For "does X work with Y", what the corpus documents about Y.

    Returns None unless the question has that shape. Otherwise:
        {"target": "linux", "documented": [{source, page, heading}],
         "procedural": [...], "nearest": ["ICU_Network_API-v1.0.50.pdf"]}

    `documented` is the point. A document whose TITLE or HEADING is about Y
    is a procedure for Y, and saying "there is a documented procedure for
    Linux" is reporting the corpus, not judging compatibility -- which is
    why this needs no new grounding contract and is safe without a model.

    The observed failure (logs, and the transcript of 2026-09-21): "does ICU
    work with linux?" was refused while retrieval returned "Accessing my
    device in Linux Environment" at rank one, and the refusal then offered
    "How do I access my ICU device in a Linux environment?" as a suggestion.
    The answer was on screen twice and the pipeline said it had nothing.

    `documented` empty is NOT an answer that it does not work -- it means we
    hold no procedure, which is all we may say. The caller falls through to
    ordinary retrieval when the question might be answerable another way
    ("does the BV30 support polymer notes?" is a spec question wearing this
    shape, and the spec tables may well answer it).

    Lived in main as _capability_reply until the switchboard; main keeps
    that name as an alias, so this moved rather than being rewritten.
    """
    from text_utils import capability_target
    target = capability_target(query)
    if not target:
        return None

    words = [w for w in re.findall(r"[a-z0-9+]{3,}", target) if w]
    if not words:
        return None

    # SPELL THE TARGET THE WAY THE DOCUMENTS DO.
    #
    # This used to match literally, on the argument that a typo silently
    # matching the wrong document is worse than falling through to
    # retrieval, "which is embedding-based and tolerant of spelling".
    # The widget transcript of 2026-09-21 showed that reasoning is
    # backwards. Asked "does ICU lite work with andorid?", retrieval was
    # tolerant exactly as predicted and returned ICU_Network_API p14 --
    # the Android procedure -- at rank 4. The only thing that failed was
    # this substring test, so the visitor was told we hold nothing, in a
    # sentence that quoted their own typo back at them.
    #
    # The correction is drawn ONLY from the words in the chunks retrieval
    # already returned for this question. That is what makes it safe:
    # the candidate set is a few hundred words that are relevant by
    # construction, not the whole corpus, so "andorid" can reach
    # "android" and has nothing unrelated to drift to.
    words, target = _as_documented(words, target, chunks)

    # TWO TIERS, kept apart because they justify different sentences.
    #
    #   documented  the target is in the document's TITLE or a HEADING --
    #               a document about Linux is a Linux procedure.
    #   procedural  the target is in the BODY of a chunk that is a numbered
    #               procedure. "1. Detect the ICU USB device. 2. Request USB
    #               permission from the Android framework. 3. Open the CDC
    #               ACM interface." is a documented Android path even though
    #               no heading says Android.
    #
    # A bare mention in prose is neither, and is not reported: "Android" in
    # a sentence about something else is not a procedure, and the whole
    # value of this feature is that it only ever reports what we hold.
    documented, procedural, nearest = [], [], []
    for c in chunks or []:
        src = c.get("source") or ""
        text = c.get("text") or c.get("document") or ""
        # The heading is the chunk's own bracketed prefix, which ingest
        # writes as "[Document - Section > Subsection] body".
        head = text[:text.index("]")] if "]" in text[:400] else ""
        body = text[len(head):]
        hit = {"source": src, "page": c.get("page"),
               "heading": head.strip("[] ")}
        if all(w in f"{src} {head}".lower() for w in words):
            if hit not in documented:
                documented.append(hit)
        elif (all(w in body.lower() for w in words)
                and len(re.findall(r"(?:^|\s)\d+\.\s+\S", body)) >= 2):
            if hit not in procedural:
                procedural.append(hit)
        elif src and src not in nearest:
            nearest.append(src)

    if not documented and not procedural and product:
        documented = _titled_for(words, product)

    return {"target": target, "documented": documented,
            "procedural": procedural, "nearest": nearest[:3]}


def _titled_for(words: list[str], product: str) -> list[dict]:
    """Documents TITLED for the target and FILED under this product.

    A last tier, consulted only when the retrieved chunks showed nothing,
    and the reason it has to exist is that the ranking cannot be fixed
    into covering this case.

    "Can I use MyCheckr with linux?" — the corpus holds "Accessing my
    device in Linux Environment", and its metadata reads
    `products: mycheckr,biometrics_general,mycheckr_mini`, so it IS a
    MyCheckr document. The document never uses the word "MyCheckr", which
    is exactly why the cross-encoder drops it: asked to score that
    passage against a question naming MyCheckr, it correctly reports a
    topic mismatch, and the MyCheckr protocol pages outrank it. Getting
    it back by forcing candidates past the rerank cut is the experiment
    this repo already ran and rejected -- widening the margin cost two
    regressions and took mean grounding from 0.993 to 0.954.

    So this does not touch ranking or context at all. It answers the
    question the capability feature actually asks -- "do we hold a
    document about Y for this product?" -- from the tagging the operator
    set when they uploaded it, which is where the answer has been all
    along. NOT catalog.product_for_source, which is a filename-substring
    mapping used for ingest defaults and reports this document as
    `biometrics_general` alone; the operator actually tagged it to
    MyCheckr and MyCheckr Mini as well.

    SCOPED TO THE PRODUCT, and that is the whole safety of it. Without
    the filter, "does the BV30 work with linux?" would find the same
    document and claim a BV30 Linux procedure that does not exist. With
    it, the document has to be filed under the product being asked about
    before anything is claimed. Verified against the real tagging:
    mycheckr and mycheckr_mini find it, bv30 and nv200s find nothing.
    """
    try:
        from retrieval_db import sources_titled_for
        found = sources_titled_for(words, product)
    except Exception as exc:
        logger.debug(f"titled-for lookup skipped: {exc}")
        return []
    out = []
    for name in found[:1]:
        logger.info("capability answered from the tagging: %r is filed "
                    "under %r", name, product)
        out.append({"source": name, "page": 1, "heading": ""})
    return out


_ASKING_FIT = re.compile(
    r"\b(best|right|most suitable|suitable|recommend|recommendation|"
    r"which one should|what should i|advise)\b")
_ABOUT_MINE = re.compile(r"\b(my|our|for me|for us)\b")


# Shortest word worth correcting. Below this a "correction" does real
# damage: "usb" is one edit from "use", "24v" from "12v".
_MIN_CORRECTABLE = 5


def _as_documented(words: list[str], target: str,
                   chunks: list[dict]) -> tuple[list[str], str]:
    """The target's words, respelled to match the retrieved passages.

    Returns (words, target) unchanged when every word already appears, or
    when nothing close enough is found -- a target we cannot place is the
    caller's cue to report nothing, exactly as before.

    Only words of >= 5 characters are corrected, and only at a 0.8
    similarity cutoff. Short tokens are where a "correction" does real
    damage: "usb" is one edit from "use", "24v" from "12v", and getting
    either wrong would have us cite a procedure for the wrong thing.
    """
    import difflib
    if not chunks:
        return words, target
    vocab = set()
    for c in chunks:
        blob = f"{c.get('source') or ''} {c.get('text') or ''}".lower()
        vocab.update(re.findall(r"[a-z0-9+]{3,}", blob))
    if not vocab:
        return words, target

    out, fixed = [], target
    for w in words:
        if w in vocab or len(w) < _MIN_CORRECTABLE:
            out.append(w)
            continue
        near = difflib.get_close_matches(w, vocab, n=1, cutoff=0.8)
        if near:
            logger.info("capability target respelled: %r -> %r "
                        "(from the retrieved passages)", w, near[0])
            out.append(near[0])
            fixed = fixed.replace(w, near[0])
        else:
            out.append(w)
    return out, fixed


def _advisory_shape(query: str) -> bool:
    """"what product would work best with my vending machine?"

    A request for a RECOMMENDATION, which is categorically different from a
    request for a documented fact: no manual contains product-selection
    guidance, so this cannot be fixed by ingesting anything.

    Deliberately narrow, and narrow on the SECOND half. sales.py already
    covers price and availability and routes them to the operator's
    deflect; this is only the selection question, which that router accepts
    and then declines through every branch. Requiring the asker's own
    situation ("my", "our") is what keeps "which products run on 24V" out
    of here -- that one IS answerable from the documents, and
    test_commercial_questions pins it.
    """
    q = " ".join((query or "").lower().split())
    if not q:
        return False
    return bool(_ASKING_FIT.search(q) and _ABOUT_MINE.search(q))


def classify(query: str, chunks: list[dict] | None = None, *,
             refused: bool = True, capability: dict | None = None,
             product: str | None = None) -> dict:
    """The one classification. Returns:

        {"kind": one of ALL_KINDS,
         "target": the capability target, when there is one,
         "capability": the capability evidence dict, or None,
         "deferral": the crossrefs deferral dict, or None,
         "why": a short operator-facing reason}

    `refused` is whether the pipeline was about to refuse. A turn that
    answered and passed grounding is STATED and nothing here second-guesses
    it -- this module exists to tell the failures apart from each other,
    not to re-open the successes.

    `capability` may be passed in when the caller has already computed it
    (query() has, for the affirmative branch) so the corpus scan is not
    repeated. It is computed here when it is not.

    ORDER IS THE DESIGN. Each outcome is checked before the ones that claim
    more:

      1. STATED               we hold a procedure. Report the corpus.
      2. DOCUMENTED_ELSEWHERE a held document names the missing one. A
                              concrete next step beats reasoning about it.
      3. INFERABLE            a compatibility question, nothing documents
                              the target, but we do hold context about the
                              product. This is the ONLY outcome that would
                              let the system say something the documents do
                              not, which is why it is next-to-last.
      4. ADVISORY             a recommendation request. Classified, not
                              routed.
      5. UNANSWERABLE         the honest default. Anything this function is
                              unsure about lands here and gets today's
                              refusal, which is the behaviour that was
                              already correct.
    """
    chunks = chunks or []
    if capability is None:
        try:
            capability = capability_evidence(query, chunks, product)
        except Exception as exc:
            logger.debug(f"answerability capability scan skipped: {exc}")
            capability = None

    target = (capability or {}).get("target")

    if not refused:
        return {"kind": STATED, "target": target, "capability": capability,
                "deferral": None, "why": "answered from the documents"}

    if capability and (capability["documented"] or capability["procedural"]):
        return {"kind": STATED, "target": target, "capability": capability,
                "deferral": None,
                "why": f"the corpus documents {target!r}"}

    deferral = None
    try:
        import crossrefs
        deferral = crossrefs.deferral_for(query, chunks)
    except Exception as exc:
        logger.debug(f"answerability deferral check skipped: {exc}")

    # Only a deferral ABOUT the question. crossrefs returns the best
    # reference in the retrieved chunks even when none is on topic, and an
    # NV200S manual page that refers jam recovery to the Service Guide sits
    # in the top chunks of many NV200S refusals; each of them then said
    # "the manual refers that to the Service Guide, which I don't hold" and
    # dropped the suggested questions -- about bezel colours.
    #
    # "About" is word overlap with the reference's own topic phrase, OR the
    # reference sits in the best-ranked passage: "screen size" shares no
    # word with "the dimensions of the device", and retrieval has already
    # judged that passage the closest thing we hold to the question.
    if deferral and (deferral.get("on_topic", True)
                     or deferral.get("rank", 0) == 0):
        return {"kind": DOCUMENTED_ELSEWHERE, "target": target,
                "capability": capability, "deferral": deferral,
                "why": f"deferred to {deferral.get('title')!r}"}

    # INFERABLE needs BOTH halves, and the second one is the guard that
    # matters. A compatibility question alone is not enough: with no
    # retrieved context there are no premises, so there is nothing to
    # reason FROM and an inference would be invention wearing a hedge.
    if capability and chunks:
        return {"kind": INFERABLE, "target": target, "capability": capability,
                "deferral": None,
                "why": f"nothing documents {target!r}; premises retrieved"}

    if _advisory_shape(query):
        return {"kind": ADVISORY, "target": None, "capability": capability,
                "deferral": None, "why": "asks for a recommendation"}

    return {"kind": UNANSWERABLE, "target": target, "capability": capability,
            "deferral": None, "why": "no route"}
