"""Import main.py with the heavy retrieval/generation modules stubbed, so
the FAQ / gaps / autogenerate routes can be exercised end-to-end (auth,
validation, persistence) without chromadb / sentence-transformers / a live
Ollama.

The stubs replace only modules main.py imports for retrieval and
generation. faq_store, catalog and widget_config are the REAL modules —
they are what is under test.

Every store path is redirected below. widget_config defaults to
widget_config.json / widget_leads.json in the CURRENT DIRECTORY, so without
these a test that saves a config or submits a lead would write into the
source tree — and widget_leads.json holds customer contact details.
"""
import sys, types, os

os.environ.setdefault("FAQ_STORE_PATH", "/tmp/apitest/data/faq.json")
os.environ.setdefault("FAQ_GAP_PATH", "/tmp/apitest/data/faq_gaps.json")
os.environ.setdefault("WIDGET_CONFIG_PATH", "/tmp/apitest/data/widget_config.json")
os.environ.setdefault("WIDGET_LEADS_PATH", "/tmp/apitest/data/widget_leads.json")
os.environ.setdefault("CATALOG_CONFIG", "/tmp/apitest/data/catalog.json")
os.environ.setdefault("ACCOUNTS_PATH", "/tmp/apitest/data/accounts.json")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-not-a-real-one")
# keystore.set_key/clear_key write to a real file — point that at a scratch
# path so a key-management test can never touch the real src/.env.
os.environ.setdefault("ENV_FILE_PATH", "/tmp/apitest/data/test.env")
# policy.py persists limit changes; point it at the scratch dir so a test
# that raises an allowance cannot leak into the real install.
os.environ.setdefault("POLICY_PATH", "/tmp/apitest/data/policy.json")
# quota.py resolves its sqlite path at import. Without this, tests write
# usage rows into the REAL src/quota.db -- which both pollutes live counters
# and makes session-cap tests depend on what previous runs left behind.
os.environ.setdefault("QUOTA_DB_PATH", "/tmp/apitest/data/quota.db")
# quota.py reads WIDGET_TOKEN_SECRET at IMPORT time, so this must be set before
# main is imported below. Without it issue_token raises and
# /admin/widget/preview_token answers 503 -- which is how test_policy.py failed
# in CI while passing locally, where src/.env supplies the real secret. A test
# secret, deliberately obvious: it signs nothing outside these tests.
os.environ.setdefault("WIDGET_TOKEN_SECRET", "test-widget-token-secret-not-a-real-one")
# backup.py reads the document store, the Chroma directory and the
# conversation DB. Without these a backup test would archive the REAL
# documents/ (~100MB of customer PDFs) and the real index -- slow, and it
# reaches outside the scratch directory every other path is confined to.
os.environ.setdefault("SOURCE_FILE_DIR", "/tmp/apitest/data/documents")
os.environ.setdefault("CHROMA_DIR", "/tmp/apitest/data/chroma_db")
os.environ.setdefault("CONVO_DB_PATH", "/tmp/apitest/data/conversations.db")
os.environ.setdefault("BACKUP_SNAPSHOT_DIR", "/tmp/apitest/data/snapshots")
# main.py's LAN-only gate checks request.client.host against this prefix
# list and 404s anything that doesn't match (see PRIVATE_NETWORKS in
# main.py) — a real, intended security control, not something to weaken.
# FastAPI's TestClient reports its socket as the literal string
# "testclient" rather than a loopback IP, so it needs to be added here
# for admin routes to be reachable at all in tests.
os.environ.setdefault("PRIVATE_NETWORKS",
    "127.,10.,192.168.,172.16.,172.17.,172.18.,172.19.,172.20.,172.21.,"
    "172.22.,172.23.,172.24.,172.25.,172.26.,172.27.,172.28.,172.29.,"
    "172.30.,172.31.,::1,testclient")
import shutil  # noqa: E402
shutil.rmtree("/tmp/apitest/data", ignore_errors=True)  # clean slate every run
os.makedirs("/tmp/apitest/data", exist_ok=True)

# ---- test accounts -----------------------------------------------------
# Admin routes want a session token now, not a shared password, so the
# harness creates one account per level and exports a ready token for each.
# Tests import these rather than signing in themselves — signing in is
# tested explicitly in test_accounts.py, and every other test only needs a
# credential that works.
import accounts as _accounts  # noqa: E402

_accounts.ALLOWED_EMAIL_DOMAIN = ""   # test emails are not company addresses

_ROOT = _accounts.bootstrap_root("root@test.local", "test-password-root")
_SUPPORT = _accounts.create_user("support@test.local", "test-password-supp",
                                 "support", created_by="harness",
                                 must_change_password=False)
_BASIC = _accounts.create_user("basic@test.local", "test-password-basic",
                               "basic", created_by="harness",
                               must_change_password=False)

ROOT_TOKEN = _accounts.issue_session(_accounts.find_by_id(_ROOT["id"]))
SUPPORT_TOKEN = _accounts.issue_session(_accounts.find_by_id(_SUPPORT["id"]))
BASIC_TOKEN = _accounts.issue_session(_accounts.find_by_id(_BASIC["id"]))


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
