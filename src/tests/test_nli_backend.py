"""NLI_BACKEND switch in grounding._get_nli_model (D-20261020-11)."""
import os
import sys
import types
from unittest import mock

import numpy as np

import grounding

_LABELS = {0: "contradiction", 1: "entailment", 2: "neutral"}


class FakeCE:
    def __init__(self, name):
        self.name = name
        self.config = types.SimpleNamespace(id2label=_LABELS)


def _run(env, fn):
    saved = (grounding._nli_model, grounding._ENTAIL_IDX, grounding._CONTRA_IDX)
    grounding._nli_model = grounding._ENTAIL_IDX = grounding._CONTRA_IDX = None
    fake_st = types.SimpleNamespace(CrossEncoder=FakeCE)
    try:
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.dict(sys.modules, {"sentence_transformers": fake_st}):
            if "NLI_BACKEND" not in env:
                os.environ.pop("NLI_BACKEND", None)
            return fn()
    finally:
        (grounding._nli_model, grounding._ENTAIL_IDX,
         grounding._CONTRA_IDX) = saved


def test_default_backend_is_pytorch():
    def go():
        with mock.patch.object(grounding, "_OnnxNLI", side_effect=AssertionError):
            assert isinstance(grounding._get_nli_model(), FakeCE)
    _run({}, go)


def test_onnx_failure_falls_back_to_pytorch():
    def go():
        with mock.patch.object(grounding, "_OnnxNLI", side_effect=OSError("offline")):
            assert isinstance(grounding._get_nli_model(), FakeCE)
    _run({"NLI_BACKEND": "onnx"}, go)


def test_onnx_backend_used_when_it_loads():
    sentinel = object()

    def go():
        with mock.patch.object(grounding, "_OnnxNLI", return_value=sentinel):
            assert grounding._get_nli_model() is sentinel
    _run({"NLI_BACKEND": "ONNX "}, go)


def test_onnx_predict_contract():
    class Session:
        def run(self, _, feed):
            assert set(feed) == {"input_ids", "attention_mask"}
            assert all(v.dtype == np.int64 for v in feed.values())
            return [np.tile([[0.0, 2.0, 1.0]], (len(feed["input_ids"]), 1))]

    def tok(a, b, **_):
        n = len(a)
        return {"input_ids": np.ones((n, 4)), "attention_mask": np.ones((n, 4)),
                "token_type_ids": np.zeros((n, 4))}

    m = object.__new__(grounding._OnnxNLI)
    m.config = types.SimpleNamespace(id2label=_LABELS)
    m.tokenizer = tok
    m._inputs = ["input_ids", "attention_mask"]
    m.session = Session()
    out = m.predict([("p", "h")] * 5, apply_softmax=True, batch_size=2)
    assert out.shape == (5, 3)
    assert np.allclose(out.sum(axis=1), 1)
    assert (out.argmax(axis=1) == 1).all()

    def go():
        grounding._nli_model = m
        assert grounding._entailment_index() == 1
    _run({}, go)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
