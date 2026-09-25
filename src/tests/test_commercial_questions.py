"""A price question reaches the operator's sales deflect.

Reported from the console test chat with sales_mode already set to
"deflect": "what is the price for a nv9 st" was answered "The NV9 Spectral
is offered at a mid-range price, delivering casino-level security", with six
manual pages cited behind it.

The configuration was correct. _CROSS carries only catalogue-navigation
vocabulary ("which products", "recommend", "suitable for"), so
is_sales_question was False, _sales_answer returned None BEFORE reading
sales_mode, and the deflect never fired.

This module tests sales.py directly -- no _harness, which stubs modules.
"""
import sales


def test_the_reported_questions_are_recognised():
    for q in ("what is the price for a nv9 st",
              "what is the exact price?",
              "how much does the NV200 cost",
              "can I get a quote for 50 units",
              "what is the lead time on the SMART Coin System",
              "where can I buy one",
              "who do I talk to about purchasing",
              "is there a reseller in Germany",
              "do you offer a discount for volume"):
        assert sales.is_commercial_question(q), q


def test_spec_questions_are_not_commercial():
    """The expensive false positive: deflecting a question the manuals DO
    answer sends a paying customer to a form for no reason."""
    for q in ("how much does it weigh",
              "how much power does it draw",
              "how much current does the NV9 Spectral need",
              "what is the supply voltage",
              "in order to reset the device, what do I press",
              "how many notes does the cashbox hold",
              "what note sizes will it take"):
        assert not sales.is_commercial_question(q), q


def test_catalogue_navigation_is_not_commercial():
    """"which products run on 24V" IS answerable from the documents and must
    keep going to the catalogue route, not the deflect."""
    for q in ("which products run on 24V",
              "what do you recommend for a kiosk",
              "which validators are suitable for unattended use"):
        assert not sales.is_commercial_question(q), q
        assert sales.is_sales_question(q), q


def test_the_two_classifiers_are_separate():
    """They answer different questions: one is "can the catalogue answer
    this", the other is "can any document answer this at all"."""
    assert sales.is_commercial_question("what is the price of the NV200")
    assert not sales.is_sales_question("what is the price of the NV200")


def test_a_commercial_question_deflects_under_the_default_mode():
    """sales_mode chooses who answers CATALOGUE questions. No mode can put a
    price into a manual that has none, so the default "answer" mode must not
    assemble one."""
    import inspect
    import main
    src = inspect.getsource(main._sales_answer)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "sales.is_commercial_question(q)" in code
    # sales_mode governs COMMERCIAL questions only: the mode is read inside
    # the `if commercial:` block, never before it, so a technical question
    # that merely sounds pre-purchase is answered under every mode.
    assert "if commercial:" in code
    assert code.index("if commercial:") < code.index('policy.value("sales_mode")')
    # "documents" is still an explicit opt-out and must be checked before the
    # deflect reply is assembled; `answer` and `deflect` both deflect.
    assert code.index('mode == "documents"') < code.index('policy.value("sales_reply")')


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
