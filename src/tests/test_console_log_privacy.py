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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
