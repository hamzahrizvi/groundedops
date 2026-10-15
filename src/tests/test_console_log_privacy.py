"""No visitor text in the process (console) log (D-20261009-06).

retention.py prunes every store except the process log, so a log line that
prints a question outlives RETENTION_DAYS. Lines carry redact() or an id.
"""
import logging
from unittest.mock import patch

import _harness  # noqa: F401 -- scratch FAQ store paths, main.py stubs
import faq_store
import main


CHUNK = {
    "id": "unit-1", "text": "The product supports USB.",
    "source": "Manual.pdf", "page": 1, "product": "product",
    "category": "", "rerank_score": 0.99,
}


def _captured(fn):
    lines = []

    class H(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    root, h = logging.getLogger(), H()
    old = root.level
    root.addHandler(h)
    root.setLevel(logging.DEBUG)
    try:
        fn()
    finally:
        root.removeHandler(h)
        root.setLevel(old)
    return "\n".join(lines)


def _ask(q):
    main.APP_STATE["ready"] = True
    with patch.object(main, "retrieve_from_db", return_value=[CHUNK]), \
         patch.object(main, "rerank", return_value=[CHUNK]), \
         patch.object(main.faq_store, "suggest_candidates",
                      return_value={"mode": "none"}), \
         patch.object(main, "route_model",
                      return_value=("accurate", ("local", "mistral"))), \
         patch.object(main, "generate_with_fallback",
                      return_value={"text": "The product supports USB.",
                                    "model": "mistral", "provider": "local"}), \
         patch.object(main, "check_grounding", return_value=(True, 0.97)), \
         patch.object(main, "_structures_for", return_value=[]), \
         patch.object(main.more_context, "build", return_value={"kind": "support"}), \
         patch.object(main, "log_interaction"):
        return main.query_route(main.QueryRequest(q=q, skip_faq=True))


def test_an_faq_miss_does_not_print_the_question():
    faq_store.add_entry("How do I clean the BV30?", "Wipe with a dry cloth.",
                        products="bv30")
    q = "zebra quartz warranty for the purple xylophone"
    captured = _captured(lambda: faq_store.suggest_candidates(q, "bv30", record=True))
    assert "xylophone" not in captured
    assert "FAQ" in captured or "gap" in captured


def test_an_answered_turn_does_not_print_the_question():
    captured = _captured(lambda: _ask("Does the product support xylophone USB?"))
    assert "xylophone" not in captured


def test_redact_keeps_length_not_text():
    from logger import redact
    r = redact("secret question")
    assert "secret" not in r and r.startswith("<15 chars #")
    assert redact("a") == redact("a")


def test_redact_tag_is_keyed_per_process():
    import pathlib
    import subprocess
    import sys
    src = pathlib.Path(__file__).resolve().parent.parent
    cmd = [sys.executable, "-c", "from logger import redact; print(redact('yes'))"]
    a, b = (subprocess.run(cmd, cwd=src, capture_output=True, text=True,
                           check=True).stdout for _ in range(2))
    assert a != b and "yes" not in a and "yes" not in b
    from logger import redact
    assert redact("yes") == redact("yes")


# The names a visitor's question travels under in the server's modules.
_VISITOR_TEXT = {"question", "resolved_query", "q", "english", "_as_typed",
                 "last_q", "frag", "query", "msg", "answer", "text"}


def _offenders(tree, name):
    """Log calls in one parsed module that print visitor text unredacted.
    Checks bare names and attribute access on a request payload, in
    positional and keyword args."""
    import ast
    bad = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in ("debug", "info", "warning",
                                       "error", "exception", "critical")):
            continue
        stack = list(call.args) + [k.value for k in call.keywords]
        while stack:
            n = stack.pop()
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") in ("redact", "len", "_digits"):
                continue
            if isinstance(n, (ast.ListComp, ast.GeneratorExp,
                              ast.SetComp, ast.DictComp)):
                continue
            if isinstance(n, ast.Name) and n.id in _VISITOR_TEXT:
                bad.append(f"{name}:{call.lineno} {n.id}")
            if (isinstance(n, ast.Attribute) and n.attr in _VISITOR_TEXT
                    and isinstance(n.value, ast.Name)
                    and n.value.id in ("payload", "req", "request", "body")):
                bad.append(f"{name}:{call.lineno} {n.value.id}.{n.attr}")
            stack.extend(ast.iter_child_nodes(n))
    return bad


def test_no_log_call_in_a_server_module_prints_the_question():
    """Every log call, not just the ones a test reaches: a call that passes
    one of these names (or an attribute of a request payload) must wrap it
    in redact(). Comprehensions are skipped (they log what matched, e.g. a
    price keyword, not the question)."""
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent
    bad = []
    for path in sorted(src.glob("*.py")):
        bad += _offenders(ast.parse(path.read_text(encoding="utf-8")), path.name)
    assert not bad, "log calls printing visitor text: " + ", ".join(bad)


def test_guard_flags_payload_attribute():
    import ast
    code = "\n".join(['logger.info("%r", payload.q)',
                      'logger.info("%s", query)',
                      'logger.info("%s", redact(payload.q))',
                      'logger.info("%s", n, extra=text)',
                      'logger.info("%d", len(text))'])
    assert _offenders(ast.parse(code), "x.py") == [
        "x.py:1 payload.q", "x.py:2 query", "x.py:4 text"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
