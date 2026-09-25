"""Conversational intents that are not questions about the documents.

Found by the scripted conversations of 2026-09-25 (tests/scenarios 19, 21,
22): "I want to talk to a person" and "I want to open a support ticket"
went through retrieval like any other question and came back as "could you
tell me more concretely what you'd like me to check", and "hello, how are
you today" was refused with three unrelated FAQ suggestions. Each of these
is a request the pipeline can recognise before it searches anything.

Two intents, each a narrow regex, checked on the RAW message:

  is_handoff_request  the visitor wants a person: an agent, a call back,
                      a ticket. The reply is the support route the widget
                      already renders on offer_support -- never a manual.
  is_greeting         the whole message is a greeting or a pleasantry with
                      no question in it. Anything longer, or with a product
                      word in it, is a question and goes to the pipeline.

Pure functions, no imports from the app, so the tests need no stubs and
_harness does not have to know about this module.
"""
from __future__ import annotations

import re

_PERSON = (r"(?:a\s+|an\s+|the\s+|some\s+|another\s+)?"
           r"(?:real\s+|actual\s+|live\s+|human\s+)?"
           r"(?:person|human|someone|somebody|agent|operator|representative"
           r"|rep|advisor|adviser|engineer|technician|colleague|member\s+of\s+staff"
           r"|support\s+team|support\s+staff|customer\s+service|customer\s+support"
           r"|help\s*desk|sales\s+team)")

_HANDOFF = re.compile(
    r"\b(?:talk|speak|chat)\s+(?:to|with)\s+" + _PERSON + r"\b"
    r"|\b(?:get|put)\s+me\s+(?:through\s+)?(?:to|in\s+touch\s+with)\s+" + _PERSON + r"\b"
    # "connect me to", "pass this on to": the object must be a person or an
    # onward route. "how do I connect it to my machine" (scenario 11) is a
    # wiring question and matched the first version of this line.
    r"|\b(?:connect|transfer|put)\s+me\s+(?:through|to|with)\b"
    r"|\b(?:pass|hand)\s+(?:me|this|it)\s+(?:on|over|along)?\s*to\s+" + _PERSON + r"\b"
    r"|\bescalat(?:e|ion)\b"
    r"|\b(?:open|raise|log|create|file|submit)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:support\s+)?"
    r"(?:ticket|case|complaint)\b"
    r"|\bcall\s*(?:me\s*)?back\b|\bring\s+me\b|\bphone\s+me\b|\bcall\s+me\b"
    r"|\bcontact\s+(?:me|details|number|email)\b"
    r"|\bhow\s+(?:do|can)\s+i\s+(?:contact|reach|email|phone|ring|call)\s+(?:you|support|someone)\b"
    r"|\b(?:i\s+(?:want|need|would\s+like|'d\s+like)\s+(?:to\s+)?)?(?:a\s+)?(?:human|person|real\s+person)\s+please\b"
    r"|\bis\s+there\s+(?:a\s+)?(?:human|person|someone)\s+(?:i\s+can|to)\b"
    r"|\b(?:not|no)\s+(?:a\s+)?(?:bot|robot|chatbot|machine)\b",
    re.I)

_GREETING = re.compile(
    r"^\W*(?:hi|hiya|hello|hey|heya|howdy|good\s+(?:morning|afternoon|evening|day)"
    r"|greetings|yo|hola|bonjour|hallo)\b"
    r"(?:\W+(?:there|again|bot|assistant|everyone|all|team))?"
    r"(?:\W+(?:how\s+are\s+you(?:\s+(?:today|doing))?|how'?s\s+it\s+going"
    r"|how\s+are\s+things|nice\s+to\s+meet\s+you|hope\s+you(?:'re|\s+are)\s+well))?"
    r"[\s!.,?]*$", re.I)


def is_handoff_request(q: str) -> bool:
    """True when the message asks for a person rather than an answer."""
    return bool(_HANDOFF.search(q or ""))


def is_greeting(q: str) -> bool:
    """True when the WHOLE message is a greeting with no question in it.

    Anchored at both ends, so "hi, how do I reset the BV30" does not match:
    that is a question with a hello in front of it and belongs to the
    pipeline. Only the bare pleasantries the pattern admits qualify.
    """
    text = " ".join((q or "").split())
    return bool(text) and len(text) <= 60 and bool(_GREETING.match(text))
