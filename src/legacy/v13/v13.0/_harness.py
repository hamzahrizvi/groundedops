"""Import main.py with the heavy retrieval/generation modules stubbed, so
the NEW v13.0 routes can be exercised end-to-end (auth, validation,
persistence) without chromadb / sentence-transformers / a live Ollama.

The stubs replace only modules main.py imports for retrieval and
generation. gaps, widget_config, faq_store and catalog are the REAL
modules — they are what is under test.
"""
import sys, types, os

os.environ.setdefault("GAPS_STORE_PATH", "/tmp/apitest/data/gaps.json")
os.environ.setdefault("WIDGET_CONFIG_PATH", "/tmp/apitest/data/widget.json")
os.environ.setdefault("WIDGET_LEADS_PATH", "/tmp/apitest/data/leads.json")
os.environ.setdefault("FAQ_STORE_PATH", "/tmp/apitest/data/faq.json")
os.environ.setdefault("CATALOG_CONFIG", "/tmp/apitest/data/catalog.json")
os.environ.setdefault("ADMIN_PASSWORD", "testpw")
os.makedirs("/tmp/apitest/data", exist_ok=True)


def stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# ---- fake vector store -------------------------------------------------
_DOCS = {}   # source -> list[(text, meta)]


class _Collection:
    def get(self, where=None, include=None, **kw):
        out = {"ids": [], "documents": [], "metadatas": []}
        for src, rows in _DOCS.items():
            if where and where.get("source") and where["source"] != src:
                continue
            for i, (text, meta) in enumerate(rows):
                out["ids"].append(f"{src}:{i}")
                out["documents"].append(text)
                out["metadatas"].append(dict(meta))
        return out

    def update(self, ids=None, metadatas=None, **kw):
        pass

    def count(self):
        return sum(len(v) for v in _DOCS.values())


def seed_doc(source, chunks, product="", category=""):
    _DOCS[source] = [(c, {"source": source, "product": product,
                          "category": category, "kind": "chunk"}) for c in chunks]


# ---- generation stub ---------------------------------------------------
GEN_RESPONSE = {"text": "", "model": "stub", "provider": "local"}


def _generate_with_fallback(role, prompt, **kw):
    return dict(GEN_RESPONSE)


# ---- stub the project modules main.py pulls in -------------------------
stub("db", get_collection=lambda: _Collection(), reset_collection=lambda: None,
     get_stats=lambda: {"count": 0}, delete_source=lambda s: {"deleted": 0},
     get_chunks_by_ids=lambda ids: [])
stub("embeddings", _get_model=lambda: None)
stub("reranker", rerank=lambda q, r, top_k=5: r[:top_k], _get=lambda: None)
stub("structure", extract_structured_block=lambda r, query=None: None)
stub("logger", log_interaction=lambda *a, **k: None)
stub("router", route_model=lambda q: ("accurate", ("local", "mistral")))
stub("grounding", check_grounding=lambda a, c, threshold=0.55: (True, 0.9),
     _get_nli_model=lambda: None)
stub("llm", generate=lambda *a, **k: dict(GEN_RESPONSE),
     generate_with_fallback=_generate_with_fallback,
     warmup_local_models=lambda m=None: {}, RETHINK_OPTIONS=[],
     condense_query=lambda q, h: q)
stub("runtime_config", get_settings=lambda: {"mode": "free"},
     set_generation_mode=lambda m: None, set_local_models_loaded=lambda v: None,
     set_online_provider=lambda p: None)
stub("memory", add_to_memory=lambda *a: None, clear_memory=lambda s: None,
     get_history=lambda s: [], get_last_query=lambda s: None)
stub("ingest", ingest_file=lambda *a, **k: {"chunks": 0})
stub("retrieval_db", retrieve_from_db=lambda *a, **k: [],
     _invalidate_bm25_cache=lambda: None)
stub("text_utils",
     passes_retrieval_gate=lambda *a, **k: True,
     retrieval_confidence_band=lambda r, t, a: ("none" if not r else "confident"),
     is_refusal=lambda a: False, is_followup_turn=lambda *a: False,
     has_domain_vocabulary=lambda q: False, has_reference_markers=lambda q: False,
     is_template_leak=lambda a: False, build_clarification_options=lambda *a: [])
stub("conversations", init_db=lambda: None, resolve_user_id=lambda u: None,
     save_turn=lambda *a, **k: None, list_conversations=lambda u: [],
     get_conversation=lambda *a: None, delete_conversation=lambda *a: True)

import main  # noqa: E402
app = main.app
