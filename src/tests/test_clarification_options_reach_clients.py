"""The clarify options must survive the trip to the surfaces people use.

They have been built on every clarify turn since v12 and read by nobody:
/query returns them, but the public widget endpoint builds its own response
dict and forwarded only the boolean, so the widget showed "which did you
mean?" as prose and the visitor had to type an answer we had already
enumerated. The console never looked at the field at all.

Three hops, each a separate hand-written list that can silently drop a
field: the /widget/ask dict, the /ask/stream meta event, and the two
clients.
"""
import inspect
import re

import _harness  # noqa: F401
import main
import widget_api


def _widget_api_source():
    return inspect.getsource(widget_api)


def test_the_public_ask_endpoint_forwards_the_options():
    src = _widget_api_source()
    assert '"clarification_options"' in src, \
        "/widget/ask builds its own dict -- an unnamed field stops here"
    # Beside the boolean, not in some unrelated branch.
    near = src[src.index('"needs_clarification": bool(result.get'):][:1200]
    assert '"clarification_options"' in near


def test_the_stream_meta_event_forwards_the_options():
    """A second, separate allowlist. The blocking endpoint and the stream
    disagreeing about what a clarify turn contains is how the widget would
    show chips on one path and not the other."""
    src = _widget_api_source()
    meta = re.search(r'yield sse\("meta", \{k: result\.get\(k\) for k in\s*\((.*?)\)\}',
                     src, re.S)
    assert meta, "the meta event moved -- update this test with it"
    assert "clarification_options" in meta.group(1)


def test_the_faq_path_answers_with_the_same_shape():
    """A client that reads the field unconditionally must not hit undefined
    on the FAQ path."""
    src = _widget_api_source()
    faq = src[:src.index('"effort": "faq_only"')]
    assert '"clarification_options": []' in faq


def test_the_widget_renders_them_as_chips():
    import pathlib
    js = (pathlib.Path(__file__).resolve().parent.parent
          / "widget" / "groundedops-widget.js").read_text(encoding="utf-8")
    assert "clarification_options" in js, "the widget must read the field"
    assert "Which did you mean?" in js
    # Rendered through the existing chip helper, not a bespoke control.
    block = js[js.index("var clarifyOpts"):][:800]
    assert "chips(" in block and "ask(opt)" in block


def test_the_console_renders_them_too():
    import pathlib
    html = (pathlib.Path(__file__).resolve().parent.parent
            / "admin.html").read_text(encoding="utf-8")
    assert "clarification_options" in html, \
        ("the test chat must match what a customer sees -- that is the one "
         "thing this page is for")
    block = html[html.index("r.clarification_options"):][:400]
    assert "send(o)" in block


def test_the_refusal_clarify_offers_product_labels_first():
    """The question this branch asks ends '...or which model you mean?', so
    the buttons have to be model names, not the visitor's own earlier
    questions."""
    src = inspect.getsource(main.query)
    branch = src[src.index("I could not pin down"):][:1800]
    assert 'build_clarification_options(\n                    "ambiguous_in_domain"' in branch \
        or '"ambiguous_in_domain", history, results' in branch
    assert '"followup", history, results' in branch, \
        "and a fallback, so the chips are never empty when there is something"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("ALL CHECKS PASSED")
