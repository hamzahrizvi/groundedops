"""Every model load is pinned to a commit and ships no remote code (D-20261021-06)."""
import os
import re
import sys
import types
from pathlib import Path
from unittest import mock

import embeddings
import grounding
import model_pins
import reranker

SRC = Path(__file__).parents[1]
REPO = Path(__file__).parents[2]
_LOAD = re.compile(r"=\s*[\w.]*(SentenceTransformer|CrossEncoder|hf_hub_download|from_pretrained)\(")


def test_every_default_model_is_pinned():
    names = {"Alibaba-NLP/gte-modernbert-base", grounding.NLI_MODEL_NAME,
             *reranker.PROFILES.values()}
    for n in names:
        assert re.fullmatch(r"[0-9a-f]{40}", model_pins.REVISIONS[n]), n


def test_loads_pass_revision_and_no_remote_code():
    found = 0
    for f in ("embeddings.py", "reranker.py", "grounding.py"):
        src = (SRC / f).read_text(encoding="utf-8")
        assert "trust_remote_code=True" not in src, f
        for line in src.splitlines():
            if _LOAD.search(line):
                found += 1
                assert "revision=" in line, line
    assert found >= 6, found


def test_loaders_use_the_pin():
    calls = []

    class Fake:
        def __init__(self, name, **kw):
            calls.append((name, kw))

    fake_st = types.SimpleNamespace(SentenceTransformer=Fake, CrossEncoder=Fake)
    saved = (embeddings._model, reranker._model, reranker._loaded, grounding._nli_model)
    embeddings._model = reranker._model = grounding._nli_model = None
    reranker._loaded = None
    try:
        with mock.patch.dict(sys.modules, {"sentence_transformers": fake_st}), \
                mock.patch.dict(os.environ):
            os.environ.pop("NLI_BACKEND", None)
            embeddings._get_model()
            reranker._get("BAAI/bge-reranker-base")
            grounding._get_nli_model()
    finally:
        embeddings._model, reranker._model, reranker._loaded, grounding._nli_model = saved
    assert len(calls) == 3
    for name, kw in calls:
        assert kw == {"revision": model_pins.revision(name)}, (name, kw)


def test_unknown_model_is_unpinned():
    assert model_pins.revision("someone/else") is None


def test_dockerfile_bakes_what_the_code_loads():
    d = (REPO / "docker" / "Dockerfile.backend").read_text(encoding="utf-8")
    for n in model_pins.REVISIONS:
        assert n in d, n
    assert "all-MiniLM-L6-v2" not in d
    assert "HF_HUB_OFFLINE=1" in d
    assert "revision=R[" in d


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
