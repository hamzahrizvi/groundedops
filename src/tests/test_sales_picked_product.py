"""A question about the picked product is not a cross-product spec query.

Office blind run 20261019f, case 38, asked in an NV9USB+ chat: "our vending
cabinet feeds the nv9usb+ 24V from the mdb side, is that ok or do I fry it?
what range does it take with and without the IF5". "what range" matched
_CROSS (a product range), "24V" matched the spec index, and the answer was
every product's supply-voltage row instead of the NV9USB+ manual's
10.8-13.2V / IF5 18-48V. 0/0/0 over three repeats.

This module tests sales.py directly -- no _harness, which stubs modules.
"""
import sales

_ROW = {"table": "Electrical", "attribute": "Supply voltage", "page": 3,
        "source": "x.pdf", "category": "Note Validators"}
INDEX = [dict(_ROW, product=k, product_name=n, values=["24 V DC"])
         for k, n in (("nv9usb", "NV9USB+"), ("nv200s", "NV200S"),
                      ("cashgenic", "CashGenic"))]

CASE_38 = ("our vending cabinet feeds the nv9usb+ 24V from the mdb side, is "
           "that ok or do I fry it? what range does it take with and without "
           "the IF5")


def test_range_alone_is_not_a_catalogue_word():
    for q in (CASE_38, "what voltage range does it accept",
              "what temperature range is it rated for"):
        assert not sales.is_sales_question(q), q
    for q in ("which of your ranges take coins",
              "what products are in the range"):
        assert sales.is_sales_question(q), q


def test_picked_product_named_alone_gets_no_spec_list():
    """Any _CROSS word ("what models") plus a quantity used to list every
    product's row even when the visitor picked and named one product."""
    q = "what models of the nv9usb+ run on 24V"
    assert sales.is_sales_question(q)
    out = sales.answer(q, [], INDEX, scoped=True, named_products=["nv9usb"],
                       scope_product="nv9usb")
    assert out is None, out


def test_cross_product_spec_questions_still_list():
    q = "I have a 24V supply. Which of your note validators can I use?"
    # Unscoped (K02 in eval_runs/20261020_sales_scope).
    assert sales.answer(q, [], INDEX)["kind"] == "spec_match"
    # Scoped, naming no product: still a fair cross-product question.
    out = sales.answer(q, [], INDEX, scoped=True, named_products=[],
                       scope_product="nv9usb")
    assert out["kind"] == "spec_match"
    # Scoped, naming a DIFFERENT product too: not only the picked one.
    out = sales.answer("which models like the nv200s or nv9usb+ run on 24V",
                       [], INDEX, scoped=True,
                       named_products=["nv200s", "nv9usb"],
                       scope_product="nv9usb")
    assert out["kind"] == "spec_match"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
