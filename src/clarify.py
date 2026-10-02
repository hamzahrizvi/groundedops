"""When to answer "which product do you mean?" instead of refusing or guessing.

TWO FAULTS, measured on 2026-09-26 on the blind sets and a probe set.

1. Vague questions were REFUSED. "It won't turn on, help?", "My machine's
   showing an error", "How do I set the age limit?" -- no product selected,
   none named -- retrieve almost nothing (best 0.002-0.094), and the
   nothing-found branch asked a clarifying question only when the question
   hit a hardcoded 35-word list tuned to the MyCheckr and MyConnect manuals
   ("device", "hub", "relay"...). A validator or hopper question could never
   match it, so all three were refused. The same list fired on SCOPED
   questions: "can MyCheckr recognise someone by their fingerprint", asked
   in a MyCheckr chat, got "which device do you mean?".

   What separates vague from off-topic, measured: every off-topic probe had
   a word the manuals never use ("cisco", "bitcoin", "france", "pasta"), or
   its words were shared by one product at most ("recommend a good laptop").
   Every vague question used only words that three or more products'
   manuals contain. So: unscoped, nothing retrieved, every content word is
   manual vocabulary, shared by at least two products, and the question
   either refers to a device ("it", "my machine") or scored above zero --
   then ask, offering exactly those products. The vocabulary is read from
   the index, so a new manual teaches it without anyone editing a list.

   UPDATE, same day: that word rule caught the three questions it was
   written from and little else. On 30 phrasings nobody had seen it asked
   on 4 of 10 vague questions (3 before it existed): "it keeps beeping",
   "the display is frozen", "my unit stopped accepting stuff" use words
   the manuals never print, and read as off-topic. Whether a sentence is
   about a coin hopper or an HP printer is a judgement about meaning, not
   vocabulary, so it is now asked of the model (classify_unscoped) -- one
   short call, only for an UNSCOPED question with weak retrieval, which in
   practice means the console and the API: widget chats always carry a
   product. The word rule stays as the fallback when no model answers.

2. Specific questions got a needless menu. "What port does the ICU local
   REST API use" scored 0.997 for MyCheckr and MyCheckr Mini -- from ONE
   shared document, so the answer is the same whichever is picked, and the
   visitor was asked anyway. near_top_products: when the top result is
   confident, only products whose passages score near it are alternatives,
   and a single document is never ambiguous with itself.
"""
from __future__ import annotations

import logging
import re
import threading

# Words that carry no subject. A question made only of these and product
# vocabulary is under-specified; they must not be counted as "in the
# manuals" or "not in the manuals".
_STOP = frozenset("""
a an the of for to in on at by with and or is are was be been it its this that
these those what whats which how do does did can could should would will may
my your our me i you we they them there here any some much many s t d ll ve re
won wont cant can't dont don't isnt doesnt please help tell means mean meaning
showing shows show set get gets keep keeps wrong right why when where who
just still again also very really thing things one ok hi hello thanks thank
""".split())

# A reference to a device the visitor has not named.
_DEVICE_REF = re.compile(
    r"\bit\b|\bthis\b|\bthat\b"
    r"|\b(?:my|the|our|this|that)\s+(?:machine|device|unit|validator|hopper|"
    r"reader|scanner|camera|screen|product|kit|system|thing|box)\b", re.I)

NEAR_TOP_MARGIN = 0.10
MAX_PRODUCT_BUTTONS = 5

_lock = threading.Lock()
_cache: dict = {"count": None, "vocab": None}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z0-9]*", (text or "").lower())


def product_vocabulary() -> dict[str, set[str]]:
    """word -> the product keys whose chunks contain it, from the index.

    Rebuilt when the collection's size changes (an upload or a delete).
    "<category>_general" buckets are left out: they hold shared documents,
    not something a visitor can pick."""
    import db
    import retrieval_db
    col = db.get_collection()
    n = col.count()
    with _lock:
        if _cache["vocab"] is not None and _cache["count"] == n:
            return _cache["vocab"]
    _, chunks = retrieval_db._get_bm25_index(col)
    vocab: dict[str, set[str]] = {}
    for c in chunks:
        keys = [k.strip() for k in (c.get("product") or "").split(",")
                if k.strip() and not k.strip().endswith("_general")]
        if not keys:
            continue
        for w in set(_words(c.get("text"))):
            vocab.setdefault(w, set()).update(keys)
    with _lock:
        _cache.update(count=n, vocab=vocab)
    return vocab


def _lookup(vocab: dict, w: str) -> set[str] | None:
    for form in (w, w.rstrip("s"), w + "s"):
        if form in vocab:
            return vocab[form]
    return None


def content_words(query: str) -> list[str]:
    return [w for w in _words(query) if len(w) > 2 and w not in _STOP]


def unscoped_clarify_products(query: str, top_score: float,
                              vocab: dict | None = None) -> list[str]:
    """Products to offer for an unscoped question retrieval could not pin
    down, or [] when the honest reply is a refusal."""
    words = content_words(query)
    if not words:
        return []
    vocab = product_vocabulary() if vocab is None else vocab
    sets = []
    for w in words:
        s = _lookup(vocab, w)
        if not s:
            return []          # a word the manuals never use: out of scope
        sets.append(s)
    products = set.intersection(*sets)
    if len(products) < 2:
        return []
    if not (_DEVICE_REF.search(query) or top_score >= 0.01):
        return []
    return sorted(products)


def near_top_products(results: list[dict], limit: int, top_score: float,
                      confident_at: float) -> list[str] | None:
    """The products that are real alternatives, or None to keep the caller's
    span. Only applies when the top result is confident: at low scores the
    ordering is noise and every product in the span stays an option.
    Returns [] when every near-top passage comes from one document."""
    if top_score < confident_at:
        return None
    near = [r for r in (results or [])[:limit]
            if (r.get("rerank_score") or 0.0) >= top_score - NEAR_TOP_MARGIN]
    if len({r.get("source") for r in near if r.get("source")}) <= 1:
        return []
    keys = []
    for r in near:
        for k in (r.get("product") or "").split(","):
            k = k.strip()
            if k and not k.endswith("_general") and k not in keys:
                keys.append(k)
    return sorted(keys)


def options_for(products: list[str], catalog: dict,
                names: dict[str, str]) -> list[dict]:
    """Buttons for a clarifying question. More than MAX_PRODUCT_BUTTONS
    products are offered as their categories instead: a category key sent
    back as the product scopes by category (see _resolve_question_scope)."""
    if len(products) <= MAX_PRODUCT_BUTTONS:
        return [{"key": k, "label": names.get(k, k)} for k in products]
    out = []
    for c in (catalog or {}).get("categories") or []:
        keys = {p.get("key") for p in c.get("products") or []}
        if keys & set(products):
            out.append({"key": c.get("key"), "label": c.get("name") or c.get("key")})
    return out


def classify_unscoped(query: str, catalog: dict) -> str | None:
    """"product" when an unscoped message is about one of the catalogue's
    products without saying which, "other" when it is about something else,
    None when no model answered (the caller falls back to the word rule).

    The catalogue's own category and product names are the only product
    knowledge given: nothing here is tuned to a corpus. A judgement call,
    so it runs inside llm.judging() -- the verifier inverted without its
    reasoning pass, and a router must not be cheaper than that."""
    import llm
    lines = []
    for c in (catalog or {}).get("categories") or []:
        names = [p.get("name") for p in c.get("products") or []
                 if p.get("name") and not (p.get("key") or "").endswith("_general")]
        if names:
            lines.append(f"- {c.get('name') or c.get('key')}: {', '.join(names)}")
    if not lines:
        return None
    msg = " ".join((query or "").split())[:400].replace('"', "'")
    prompt = (
        "You route messages for a customer-support assistant that covers ONLY "
        "these products:\n" + "\n".join(lines) + "\n\n"
        f'Customer message: "{msg}"\n\n'
        "Answer with exactly one word.\n"
        "PRODUCT - the message is about one of these products or something they "
        "do: a fault, symptom, light, sound, error, setting, part, installation, "
        "cleaning or maintenance - even if it does not say which product.\n"
        "OTHER - the message is about something else: another company's device, "
        "a phone, computer, router or printer, an order, delivery or account, "
        "general knowledge, or small talk.")
    try:
        with llm.judging():
            out = llm.generate_with_fallback("fast", prompt)
    except Exception:
        return None
    if not out or out.get("provider") in (None, "none"):
        return None
    m = re.search(r"\b(PRODUCT|OTHER)\b", (out.get("text") or "").upper())
    return m.group(1).lower() if m else None


def described_product(query: str, candidates: list[str], results: list[dict],
                      names: dict[str, str]) -> str | None:
    """The one candidate the question DESCRIBES without naming, or None.

    8.17. "Which age-check device has no screen at all?" retrieved both
    MyCheckr manuals near the top and was asked which it meant, although
    only the Mini has no screen. Words cannot settle that ("screen" is in
    both manuals; the question says "no screen"), so the model is shown
    each candidate's own retrieved passages and asked. Only consulted where
    the "which did you mean?" menu would otherwise be shown; None (unsure,
    no model, an unparseable reply) shows the menu exactly as before.
    """
    import llm
    blocks, by_name = [], {}
    for k in candidates:
        name = names.get(k) or k
        by_name[name.lower()] = k
        texts = [" ".join((r.get("text") or "").split())[:500]
                 for r in results or []
                 if k in [p.strip() for p in (r.get("product") or "").split(",")]][:2]
        if texts:
            blocks.append(f'Product "{name}":\n' + "\n".join(f"- {t}" for t in texts))
    if len(blocks) < 2:
        return None
    msg = " ".join((query or "").split())[:400].replace('"', "'")
    prompt = (
        "A customer asked a support assistant a question without naming the "
        "product. These passages come from each candidate product's manual.\n\n"
        + "\n\n".join(blocks) + "\n\n"
        f'Customer message: "{msg}"\n\n'
        "Does the message describe ONE of these products by a feature, "
        "specification or trait that the passages show only that product "
        "has? Answer with exactly that product's name, nothing else. If the "
        "message could apply to more than one of them, answer NONE.")
    try:
        with llm.judging():
            out = llm.generate_with_fallback("fast", prompt)
    except Exception:
        return None
    if not out or out.get("provider") in (None, "none"):
        return None
    reply = (out.get("text") or "").strip().strip(".\"'*` ").lower()
    logging.getLogger(__name__).info("described_product %s -> %r", candidates, reply[:60])
    return by_name.get(reply)


def candidate_products(query: str, vocab: dict | None = None) -> list[str]:
    """Products worth offering for a vague question: those whose manuals
    use every recognised word of it, else any of them, else all products.
    options_for turns a long list into category buttons."""
    vocab = product_vocabulary() if vocab is None else vocab
    sets = [s for s in (_lookup(vocab, w) for w in content_words(query)) if s]
    if sets:
        inter = set.intersection(*sets)
        if len(inter) >= 2:
            return sorted(inter)
        union = set.union(*sets)
        if len(union) >= 2:
            return sorted(union)
    everything = set()
    for s in vocab.values():
        everything |= s
    return sorted(everything)
