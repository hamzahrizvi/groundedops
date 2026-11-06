"""grounding._get_nli_model loads the pinned CrossEncoder (D-20261105-01)."""
import sys
import types
from unittest import mock

import grounding
import model_pins

_LABELS = {0: "contradiction", 1: "entailment", 2: "neutral"}


class FakeCE:
    def __init__(self, name, **kw):
        self.name = name
        self.kw = kw
        self.config = types.SimpleNamespace(id2label=_LABELS)


def _run(fn):
    saved = (grounding._nli_model, grounding._ENTAIL_IDX, grounding._CONTRA_IDX)
    grounding._nli_model = grounding._ENTAIL_IDX = grounding._CONTRA_IDX = None
    fake_st = types.SimpleNamespace(CrossEncoder=FakeCE)
    try:
        with mock.patch.dict(sys.modules, {"sentence_transformers": fake_st}):
            return fn()
    finally:
        (grounding._nli_model, grounding._ENTAIL_IDX,
         grounding._CONTRA_IDX) = saved


def test_loads_pinned_cross_encoder_once():
    def go():
        m = grounding._get_nli_model()
        assert isinstance(m, FakeCE)
        assert m.name == grounding.NLI_MODEL_NAME
        assert m.kw == {"revision": model_pins.revision(grounding.NLI_MODEL_NAME)}
        assert grounding._get_nli_model() is m
    _run(go)


def test_entailment_column_read_from_model_config():
    def go():
        assert grounding._entailment_index() == 1
    _run(go)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
