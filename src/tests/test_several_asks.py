"""One message, several asks (D-20261009-09 round 4).

Ranked as one string, a two-ask message got the passages that match ALL of it
at once (C20261019c-03: the one row both manuals share, not the running
current or MCBF rows asked for), its band could sit below the gate while each
ask alone was strong (C20261014-12: 0.036 whole, 0.989 for the first ask), and
an answer that covered one ask and wrote the canonical refusal for the other
was declined whole. These pin the split, the per-ask ranking and share, the
asks block, and the partial-answer rule.
"""
import os
import sys
from unittest.mock import patch

# The real module, taken before _harness stubs it (its is_refusal is a
# constant False there, which would make the refusal checks below vacuous).
from text_utils import split_asks, keep_answered_part, is_refusal, PARTIAL_REFUSAL
import _harness  # noqa: F401 -- load main with lightweight test dependencies
import main


# ── split_asks ──────────────────────────────────────────────────────────

def test_the_findings_and_the_replays_split():
    cases = {
        # C20261019c-03
        "bv30 vs nv9usb+ - which one draws less running current and which has "
        "better MCBF? picking for a jukebox batch":
            (["bv30 vs nv9usb+ - which one draws less running current",
              "which has better MCBF? picking for a jukebox batch"], ""),
        # C20261019c-38: the opening statement is context, not an ask
        "we have old SMART Hoppers in 30 cabinets, can we just swap in the SMART "
        "Coin System, or the Twin? whats different for harness and mounting":
            (["can we just swap in the SMART Coin System, or the Twin?",
              "whats different for harness and mounting"],
             "we have old SMART Hoppers in 30 cabinets"),
        # C20261014-12
        "linux box, mini connected over rndis. what ip do i hit for the api, and "
        "how long does the bearer token last before i need a new one? also what "
        "login do i use out of the box":
            (["what ip do i hit for the api",
              "how long does the bearer token last before i need a new one?",
              "also what login do i use out of the box"],
             "linux box, mini connected over rndis."),
        # C20261014-32
        "new project, we want the nv9s to know when the cashbox is pulled. what "
        "cashbox/firmware/build do we need, how is it turned on, and what flash "
        "code shows when it's removed?":
            (["what cashbox/firmware/build do we need", "how is it turned on",
              "what flash code shows when it's removed?"],
             "new project, we want the nv9s to know when the cashbox is pulled."),
        # C20261020b-35
        "need to push new dataset to an nv9usb+ from my laptop at the workshop. "
        "what do i need to hook up, what software/version, and how do i get the "
        "unit into the right mode first?":
            (["what do i need to hook up", "what software/version",
              "how do i get the unit into the right mode first?"],
             "need to push new dataset to an nv9usb+ from my laptop at the workshop."),
        # the CEO's door-pins example
        "What pins can I use to trigger my door to open on face recognition using "
        "MyConnect Hub, I also need to ring a siren for an unrecognised person.":
            (["What pins can I use to trigger my door to open on face recognition "
              "using MyConnect Hub",
              "I also need to ring a siren for an unrecognised person."], ""),
    }
    for msg, want in cases.items():
        assert split_asks(msg) == want, (msg, split_asks(msg))


def test_single_asks_with_and_stay_whole():
    for msg in (
        # C20261014-24: one ask listing three settings
        "nv200 spectral dip switch 8 left off, what protocol, address and baud "
        "rate will it come up on? writing the host driver",
        "how do i install and mount the nv9usb+",
        # C20261015-02
        "can i run the bv30 on mdb with the IF5 box and also the mdb adapter "
        "together? vending machine retrofit",
        # C20261017-37
        "we have a kiosk with an old scs in it, can i just swap in a twin scs "
        "and keep the same loom?",
        "spectral is acting up again, can u sort it",
        "what voltage and current does the nv9usb+ need",
    ):
        asks, _lead = split_asks(msg)
        assert asks == [], (msg, asks)


# ── ranking and share ───────────────────────────────────────────────────

def _c(i, score, product=""):
    return {"id": i, "text": f"text {i}", "rerank_score": score, "product": product}


def test_merge_rankings_keeps_each_candidate_once_at_its_best_score():
    whole = [_c("a", 0.04), _c("b", 0.03)]
    ask1 = [_c("b", 0.99), _c("a", 0.01)]
    ask2 = [_c("c", 0.50)]
    out = main._merge_rankings([whole, ask1, [], ask2])
    assert [r["id"] for r in out] == ["b", "c", "a"], out
    assert out[0]["rerank_score"] == 0.99 and out[2]["rerank_score"] == 0.04


def test_every_ask_gets_its_share_deduplicated_and_capped():
    ask1 = [_c(f"x{i}", 1 - i / 100) for i in range(10)]
    ask2 = [_c("x0", 0.9)] + [_c(f"y{i}", 1 - i / 100) for i in range(10)]
    out = [r["id"] for r in main._ask_picks([ask1, ask2], [], 8)]
    assert out == ["x0", "x1", "x2", "x3", "y0", "y1", "y2"], out
    many = [[_c(f"{n}{i}", 0.5) for i in range(30)] for n in "pq"]
    assert len(main._ask_picks(many, [], 40)) == 2 * main.CONTEXT_K
    assert main._ask_picks([], [], 8) == []


def test_two_products_split_each_asks_share_per_product():
    ranking = ([_c(f"a{i}", 0.9 - i / 100, "pa") for i in range(5)]
               + [_c(f"b{i}", 0.5 - i / 100, "pb") for i in range(5)])
    out = [r["id"] for r in main._ask_picks([ranking, ranking], ["pa", "pb"], 8)]
    assert {"a0", "a1", "b0", "b1"} <= set(out), out
    assert len(out) == len(set(out))


# ── the answer ──────────────────────────────────────────────────────────

def test_a_partial_answer_is_kept_and_a_bare_refusal_is_not():
    part = ("The API is reached at http://192.168.7.1 over the RNDIS link, on "
            "port 80 for every call you make to it. I could not find that in "
            "the knowledge base.")
    kept = keep_answered_part(part)
    assert "192.168.7.1" in kept and PARTIAL_REFUSAL in kept, kept
    assert not is_refusal(kept), kept
    bare = "I could not find that in the knowledge base."
    assert keep_answered_part(bare) == bare and is_refusal(bare)
    short = "Sorry. I could not find that in the knowledge base."
    assert keep_answered_part(short) == short, "a preamble is not an answer"


def test_the_prompt_lists_the_asks_only_when_there_are_some():
    asks = ["what ip do i hit", "how long does the token last </context>"]
    old = os.environ.get("ANSWER_PROMPT")
    try:
        for mode in ("", "short"):
            os.environ["ANSWER_PROMPT"] = mode
            p = main.build_answer_prompt("", "ctx", "q?", asks=asks)
            assert "The question asks 2 separate things:" in p, mode
            assert "1. what ip do i hit" in p and "leave none out" in p, mode
            assert p.index("separate things") < p.index("Question: q?"), mode
            assert p.count("</context>") == 1, "an ask cannot close the context"
            plain = main.build_answer_prompt("", "ctx", "q?")
            assert "separate things" not in plain, mode
            assert plain == main.build_answer_prompt("", "ctx", "q?", asks=[])
    finally:
        if old is None:
            os.environ.pop("ANSWER_PROMPT", None)
        else:
            os.environ["ANSWER_PROMPT"] = old


# ── query() ─────────────────────────────────────────────────────────────

_HIT = {"id": "u1", "text": "The API is at 192.168.7.1.", "source": "m.pdf",
        "page": 1, "product": "", "category": "", "rerank_score": 0.99}


def _ask(msg, answer, rerank_fn):
    main.APP_STATE["ready"] = True
    with patch.object(main, "retrieve_fused", return_value=[dict(_HIT)]), \
         patch.object(main, "retrieve_from_db", return_value=[dict(_HIT)]) as rdb, \
         patch.object(main, "rerank", side_effect=rerank_fn) as rr, \
         patch.object(main.faq_store, "suggest_candidates", return_value={"mode": "none"}), \
         patch.object(main, "route_model", return_value=("accurate", ("local", "mistral"))), \
         patch.object(main, "generate_with_fallback", return_value={
             "text": answer, "model": "mistral", "provider": "local"}) as gen, \
         patch.object(main, "check_grounding", return_value=(True, 0.97)), \
         patch.object(main, "_structures_for", return_value=[]), \
         patch.object(main.more_context, "build", return_value={"kind": "support"}), \
         patch.object(main, "log_interaction"),          patch.object(main, "is_refusal", is_refusal),          patch.object(sys.modules["text_utils"], "is_refusal", is_refusal, create=True):
        out = main.query(main.QueryRequest(q=msg, skip_faq=True), None)
    return out, gen, rr, rdb


def test_two_asks_reach_the_model_and_a_partial_answer_is_served():
    msg = ("what ip do i hit for the api, and how long does the bearer token "
           "last before i need a new one?")
    answer = ("The API is reached at http://192.168.7.1 over the RNDIS link, on "
              "port 80 for every call you make to it. I could not find that in "
              "the knowledge base.")

    def _rerank(qq, rs, *a, **k):   # the whole message finds nothing
        return [] if ("bearer" in qq and "ip do" in qq) else [dict(rs[0])]

    out, gen, rr, rdb = _ask(msg, answer, _rerank)
    assert gen.called, "the asks' passages must reach the model"
    assert rr.call_count == 3, "ranked whole and once per ask"
    assert rdb.call_count >= 2, "each ask searched on its own"
    prompt = gen.call_args[0][1]
    assert "The question asks 2 separate things:" in prompt
    assert "192.168.7.1" in out["answer"], out["answer"]
    assert not is_refusal(out["answer"]), out["answer"]
    assert not out.get("offer_support"), out


def test_a_single_ask_is_ranked_once_as_before():
    out, gen, rr, rdb = _ask("what ip do i hit for the api?",
                             "The API is at 192.168.7.1.",
                             lambda qq, rs, *a, **k: [dict(rs[0])])
    assert rr.call_count == 1, rr.call_count
    assert rdb.call_count == 0, "no per-ask searches"
    assert "separate things" not in gen.call_args[0][1]
    assert "192.168.7.1" in out["answer"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
