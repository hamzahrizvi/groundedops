"""A two-product comparison answered in steps on the widget (v17.4 trial).

"Compare the NV9USB+ and the BV30 power supply" becomes three answers that
reach the visitor one by one: the NV9USB+ on its own, the BV30 on its own,
then the comparison from the normal pipeline. Each part is a full, grounded
answer through /widget/ask (gates and credits included), so a comparison
costs three answers. WIDGET_STEPWISE_COMPARE=0 turns it off.

plan() only decides and words the per-product questions; widget_api's
/ask/stream runs them.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

SPLIT_PROMPT = """A customer asked a question comparing two products:

{q}

Rewrite it as two separate questions, one about each product, keeping exactly
what the customer wants to know. Each question names only its own product.
Reply with exactly two lines and nothing else:
1. <question about {a}>
2. <question about {b}>"""


def enabled() -> bool:
    return os.getenv("WIDGET_STEPWISE_COMPARE", "1") != "0"


def products(q: str) -> list[str]:
    """The two catalogue product keys a comparison question names, else []."""
    from main import _is_comparison, _product_names, _products_named_in
    if not _is_comparison(q):
        return []
    named = list(dict.fromkeys(_products_named_in(q, list(_product_names()))))
    return named if len(named) == 2 else []


def parse_split(text: str, names: list[str]) -> list[str] | None:
    """The two numbered lines of the model's reply, each naming only its own
    product (by display name, case-insensitive); None if the reply is not
    that shape. Pure."""
    lines = [re.sub(r"^\s*\d[.)]\s*", "", ln).strip()
             for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) != 2:
        return None
    for line, own, other in ((lines[0], names[0], names[1]),
                             (lines[1], names[1], names[0])):
        low, own, other = line.lower(), own.lower(), other.lower()
        # "NV200" sits inside "NV200 Spectral": not a mention of the other.
        if own not in low or (other in low and other not in own):
            return None
    return lines


def plan(q: str) -> list[tuple[str, str, str]]:
    """[(product key, display name, its own question)] x2 for a comparison of
    two products, else []. Any failure means [] and the normal single answer."""
    if not enabled():
        return []
    try:
        keys = products(q)
        if not keys:
            return []
        from main import _product_names
        import llm
        names = [_product_names().get(k, k) for k in keys]
        out = llm.generate_with_fallback(
            "default", SPLIT_PROMPT.format(q=q, a=names[0], b=names[1]))
        if out.get("provider") == "none":
            return []
        lines = parse_split(out.get("text", ""), names)
        if not lines:
            logger.info(f"stepwise: split not usable for {q!r}: {out.get('text')!r}")
            return []
        return list(zip(keys, names, lines))
    except Exception as exc:
        logger.warning(f"stepwise: plan skipped ({exc})")
        return []


if __name__ == "__main__":
    n = ["NV9USB+", "BV30"]
    assert parse_split("1. What supply does the NV9USB+ need?\n"
                       "2. What supply does the BV30 need?", n) == [
        "What supply does the NV9USB+ need?", "What supply does the BV30 need?"]
    assert parse_split("1. NV9USB+ vs BV30 supply?\n2. BV30 supply?", n) is None
    assert parse_split("Only one line about the NV9USB+", n) is None
    assert parse_split("1) Size of the NV200?\n2) Size of the NV200 Spectral?",
                       ["NV200", "NV200 Spectral"]) == [
        "Size of the NV200?", "Size of the NV200 Spectral?"]
    print("ok")
