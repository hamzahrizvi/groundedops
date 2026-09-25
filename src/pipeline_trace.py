"""Where a question actually went, and where it stopped.

WHAT THIS IS FOR. The console's Pipeline check could tell you *that* a
question failed -- a refusal, a clarifying question back, a suppressed
answer -- but not *where*. Every failure looked the same from the outside,
so diagnosing one meant reading backend.log against a 1300-line function
and guessing which branch had fired. The answer text is the same whether
retrieval found nothing, retrieval found too much and spanned two products,
the model declined, or the verifier could not stand the answer up; those
are four different problems with four different fixes.

So the pipeline records the stages it passes through and stops on, each
with a plain-English label and a note carrying the numbers that decided it.
The last stage recorded IS where the turn ended -- every terminal branch
marks itself immediately before returning -- so "where did it fail" is a
lookup, not an inference.

DESIGN RULES, both load-bearing:

  * Tracing must never break an answer. Every public function here
    swallows its own exceptions. A tracing bug costs the diagnostic, not
    the response.
  * The labels are the customer-facing vocabulary from tools/pipeline_map.html,
    not the function names. The people who read this in the console are not
    the people who wrote main.py, and "retrieval_confidence_band == none"
    tells them nothing that "nothing in the manuals matched closely enough"
    does not.

Used by main.py's /query, and rendered under each answer in the console's
Pipeline check panel.
"""
from __future__ import annotations

import time
from contextvars import ContextVar

# id -> what to call this step in front of somebody who has not read the code.
# Ids match tools/pipeline_map.html so the two stay readable side by side.
LABELS: dict[str, str] = {
    # understanding the question
    "entry":       "Took the question",
    "condense":    "Turned a follow-up into a full question",
    "followup":    "Pieced the question together from the last turn",
    "scope":       "Applied the product we are talking about",
    "alias":       "Swapped nicknames for the names used in the manuals",
    "phrasings":   "Chose the wordings to search for",
    # answering without a search
    "steps":       "Served the steps it had offered",
    "faq.picked":  "Served the ready-made answer they picked",
    "more":        "Expanded the previous answer",
    "sales":       "Answered from the product catalogue",
    "faq.answer":  "Served a hand-written answer",
    "faq.ask":     "Offered hand-written answers to choose from",
    # finding the evidence
    "retrieve":    "Searched the manuals",
    "exclude":     "Dropped internal-only documents",
    "rerank":      "Sorted the results more carefully",
    "band":        "Judged how good the results were",
    "none.follow": "Gave up on a follow-up and asked what to expand on",
    "none.vague":  "Could not tell which device was meant",
    "none.reject": "Found nothing relevant and said so",
    "span.ask":    "Evidence spanned several products, so asked which",
    # writing
    "route":       "Picked the model for this kind of question",
    "extract":     "Copied a checklist straight out of the manual",
    "context":     "Chose which passages to answer from",
    "reanswer":    "Re-read the passages after 'that did not answer it'",
    "procedures":  "Fetched steps the search had missed",
    "generate":    "Wrote the answer",
    # checking
    "ground":      "Checked the answer against the manual",
    "escalate":    "Retried on the backup provider",
    "retry":       "Wrote it again and re-checked",
    "verbatim":    "Served the table itself instead of prose",
    "suppress":    "Held back an answer it could not stand behind",
    # what a refusal becomes
    "switchboard": "Worked out what kind of question this was",
    "capability":  "Found it documented after all, and offered the steps",
    "inference":   "Answered by inference, under the opt-in contract",
    "clarify":     "Asked one question back",
    "refusal":     "Said no honestly, and offered a person",
    # done
    "respond":     "Answered",
}

_current: ContextVar[dict | None] = ContextVar("pipeline_trace", default=None)


def start():
    """Begin a trace for this request. Returns a token for reset()."""
    try:
        return _current.set({"t0": time.time(), "stages": []})
    except Exception:
        return None


def reset(token) -> None:
    if token is not None:
        try:
            _current.reset(token)
        except Exception:
            pass


def mark(stage: str, note: str | None = None) -> None:
    """Record that the turn reached `stage`.

    `note` is the evidence: the score that decided it, how many passages
    survived, which provider answered. It is what turns "it refused" into
    "it refused because the best match scored 0.204".
    """
    try:
        cur = _current.get()
        if cur is None:
            return
        cur["stages"].append({
            "id": stage,
            "label": LABELS.get(stage, stage),
            "note": note,
            "at_ms": round((time.time() - cur["t0"]) * 1000),
        })
    except Exception:
        pass


# The stages that are an OUTCOME rather than a step along the way. "respond"
# is one of them, but the weakest: it says only that a reply was sent, which
# is true of a clarifying question and a refusal too. So a more specific
# outcome recorded later in the turn wins over it -- a capability offer is
# what happened, "answered" is merely that something came back.
OUTCOMES = frozenset({
    "steps", "faq.picked", "faq.answer", "faq.ask", "more", "sales",
    "extract", "none.follow", "none.vague", "none.reject", "span.ask",
    "verbatim", "suppress", "capability", "inference", "clarify",
    "refusal", "respond",
})


def snapshot() -> dict | None:
    """The trace so far: every stage, and the one that decided the turn.

    `exit` is the last OUTCOME stage other than "respond", falling back to
    "respond" and then to whatever was recorded last. Every branch that
    returns marks itself, so this reports where the turn actually ended
    rather than reconstructing it afterwards from the role and the flags --
    which could not tell "nothing matched" from "the model declined".
    """
    try:
        cur = _current.get()
        if cur is None:
            return None
        stages = cur["stages"]
        decisive = [s for s in stages
                    if s["id"] in OUTCOMES and s["id"] != "respond"]
        fallback = [s for s in stages if s["id"] == "respond"]
        exit_stage = (decisive[-1] if decisive else
                      fallback[-1] if fallback else
                      stages[-1] if stages else None)
        return {
            "stages": stages,
            "exit": exit_stage,
            "ms": round((time.time() - cur["t0"]) * 1000),
        }
    except Exception:
        return None
