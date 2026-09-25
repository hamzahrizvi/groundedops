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
import shutil, tempfile, atexit, time

# ---- one scratch directory, private to THIS process ---------------------
# Every store path below lives in here. It used to be a FIXED shared path
# (/tmp/apitest/data, which on Windows is C:\tmp\apitest\data) that was
# wiped with shutil.rmtree at IMPORT time. run_tests.py runs harness files
# as separate processes, so any overlap at all -- a second run_tests.py, a
# leftover subprocess, a developer running one file while the suite runs --
# deleted the other's accounts.json mid-test. Every admin route then
# answered 401, and reading request_rows[0] off an emptied store raised
# IndexError. Each file still passed when run alone, which is exactly what
# made it look like flake rather than a shared-state bug.
#
# mkdtemp gives a fresh, unique directory per process: nothing to wipe at
# import, and nothing another process can reach.
#
# The paths below are ASSIGNED, not setdefault-ed, and that is load-bearing.
# run_tests.py spawns own-process tests with env=dict(os.environ), so a name
# already present in the parent -- a developer's shell export, or a test
# that set one before it was given its own process -- silently won, and the
# child wrote to the inherited location instead of its own. That is how
# ENV_FILE_PATH once reached the real src/.env. These names address this
# process's private directory; nothing outside it may redirect them.
#
# GO_TEST_SCRATCH_DIR pins the location instead, for CI collecting
# artifacts. A pinned directory belongs to the caller, so it is created but
# never wiped -- and pointing two concurrent processes at one reproduces
# the original bug by hand.
#
# dir="/tmp" (C:\tmp on Windows), NOT the system TEMP mkdtemp would pick on
# its own. keystore writes atomically via os.replace, and per the note in
# tests/test_model_routing.py that raised PermissionError intermittently
# against a directory under the system TEMP. Everything here has been
# written under C:\tmp all along, so staying on that volume keeps the
# finding that cost someone an afternoon while still giving each process a
# directory of its own.
def _sweep_stale(parent, prefix, max_age_s=2 * 60 * 60):
    """Delete scratch directories left behind by runs that are long over.

    atexit cannot always finish the job. quota.db is sqlite and its
    connection is still open at interpreter shutdown, so on Windows the
    unlink fails, rmtree(ignore_errors=True) gives up, and the directory
    survives holding one 16K file. One per harness process per run adds up
    fast -- twelve after a single afternoon.

    Age, not liveness. The obvious check is os.kill(pid, 0), but on Windows
    Python that routes to TerminateProcess and would KILL the process it
    was meant to ask about -- including a developer's dev server, if a pid
    happened to be reused. Nothing here is worth that. A directory still in
    use has its mtime bumped as files are written, and a suite run lasts
    minutes, so a two-hour floor cannot reach a live one.
    """
    now = time.time()
    try:
        names = os.listdir(parent)
    except OSError:
        return
    for name in names:
        if not name.startswith(prefix):
            continue
        victim = os.path.join(parent, name)
        try:
            if not os.path.isdir(victim):
                continue
            if now - os.path.getmtime(victim) < max_age_s:
                continue
        except OSError:
            continue
        shutil.rmtree(victim, ignore_errors=True)


_PINNED = os.environ.get("GO_TEST_SCRATCH_DIR")
if _PINNED:
    _SCRATCH = os.path.abspath(_PINNED)
    os.makedirs(_SCRATCH, exist_ok=True)
else:
    os.makedirs("/tmp", exist_ok=True)
    _sweep_stale("/tmp", "apitest-")
    _SCRATCH = tempfile.mkdtemp(prefix="apitest-%d-" % os.getpid(), dir="/tmp")
    atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)


def _scratch(name):
    return os.path.join(_SCRATCH, name)


os.environ["FAQ_STORE_PATH"] = _scratch("faq.json")
os.environ["FAQ_GAP_PATH"] = _scratch("faq_gaps.json")
os.environ["WIDGET_CONFIG_PATH"] = _scratch("widget_config.json")
os.environ["WIDGET_LEADS_PATH"] = _scratch("widget_leads.json")
os.environ["CATALOG_CONFIG"] = _scratch("catalog.json")
os.environ["ACCOUNTS_PATH"] = _scratch("accounts.json")
os.environ["ACCOUNT_REQUESTS_PATH"] = _scratch("account_requests.json")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-not-a-real-one")
# keystore.set_key/clear_key write to a real file — point that at a scratch
# path so a key-management test can never touch the real src/.env.
os.environ["ENV_FILE_PATH"] = _scratch("test.env")
os.environ["SECRETS_VAULT_PATH"] = _scratch("secrets.enc")
# policy.py persists limit changes; point it at the scratch dir so a test
# that raises an allowance cannot leak into the real install.
os.environ["POLICY_PATH"] = _scratch("policy.json")
# credit_watch: alert state in scratch, and no background balance checker
# making real provider calls from inside a test run.
os.environ["CREDIT_ALERT_STATE_PATH"] = _scratch("credit_alerts.json")
os.environ["CREDIT_CHECK_HOURS"] = "0"
os.environ["AUTH_TOKENS_PATH"] = _scratch("auth_tokens.json")
# quota.py resolves its sqlite path at import. Without this, tests write
# usage rows into the REAL src/quota.db -- which both pollutes live counters
# and makes session-cap tests depend on what previous runs left behind.
os.environ["QUOTA_DB_PATH"] = _scratch("quota.db")
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
os.environ["SOURCE_FILE_DIR"] = _scratch("documents")
os.environ["CHROMA_DIR"] = _scratch("chroma_db")
os.environ["CONVO_DB_PATH"] = _scratch("conversations.db")
os.environ["BACKUP_SNAPSHOT_DIR"] = _scratch("snapshots")
# Forced empty, NOT setdefault: main.py loads the developer's real src/.env
# at import and would otherwise leak their local BACKUP_ALLOW_PLAINTEXT in,
# breaking the "a passphrase is required" assertions on whichever machine
# has it switched on. _load_env_file skips keys already in os.environ, so
# setting it empty here wins. Tests that want the plaintext path set it
# themselves, explicitly.
os.environ["BACKUP_ALLOW_PLAINTEXT"] = ""
# BACKUP_PASSPHRASE needs the same treatment, and for a sharper reason: the
# export endpoint falls back to it when a request omits one, which is the
# whole point of the unattended/cron path. So on any machine that has set up
# the daily backup, "exporting with no passphrase is refused" stopped being
# true -- the export quietly succeeded using the environment's passphrase
# and returned a binary archive where the test expected a JSON error.
# Found exactly that way: the setup instructions were followed, and this
# suite started failing on a passing tree.
os.environ["BACKUP_PASSPHRASE"] = ""
# Provider keys and their role assignments need the same forcing, and the
# reason is the same: ENV_FILE_PATH above only redirects what keystore
# WRITES. main.py still loads the developer's real src/.env into os.environ
# at import, so a key present there made "an unset provider masks to None"
# fail on their machine while passing in CI.
#
# Blanked, not popped — _load_env_file skips keys already in os.environ, and
# popping would let the real value back in when main is imported below. Every
# keystore read strips before testing, so "" is indistinguishable from unset.
for _leaky in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
               "PROVIDER_ROLE_DEFAULT", "PROVIDER_ROLE_ADVANCED",
               "PROVIDER_ROLE_BACKUP"):
    os.environ[_leaky] = ""
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
# retag_product/delete_by_product/count_by_product return 0 here: the fake
# collection has no product metadata to rewrite. The product-deletion tests
# assert on the FAQ and catalogue halves, which are the REAL modules -- the
# vector-store half is covered by db.py's own logic, not by this stub.
stub("db", get_collection=lambda: _Collection(), reset_collection=lambda: None,
     get_stats=lambda: {"count": 0}, delete_source=lambda s: {"deleted": 0},
     get_chunks_by_ids=lambda ids: [],
     retag_product=lambda old, new: 0, delete_by_product=lambda k: 0,
     count_by_product=lambda k: 0)
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
# complete_procedures is a PASS-THROUGH, not a no-op returning []. It is
# additive by contract -- it hands back the chunks it was given plus any
# steps they point at -- so a stub that dropped them would empty the
# context of every API test that feeds chunks in, and the tests would
# fail somewhere far from here. Added 2026-09-22 with the function; a
# stub missing a new name fails at IMPORT ("unknown location", because a
# types.ModuleType has no __file__), which took out nine files at once.
stub("retrieval_db", retrieve_from_db=lambda *a, **k: [],
     retrieve_fused=lambda *a, **k: [],
     fuse_ranked_results=lambda result_sets, *a, **k: (
         list(result_sets[0]) if result_sets else []),
     apply_arm_guarantee=lambda ranked_ids, keep_n, *a, **k: list(
         ranked_ids[:keep_n]),
     complete_procedures=lambda chunks, *a, **k: chunks,
     _invalidate_bm25_cache=lambda: None)
# text_utils is stubbed to pin GATE behaviour, not because it is heavy -- it
# imports nothing but `re`. So stem() is handed through for real rather than
# faked: more_context.py depends on it to tell new material from a reworded
# restatement, and a lambda here would silently change that judgement in
# every API test. Imported before the stub replaces the module; the function
# object stays valid afterwards.
from text_utils import stem as _real_stem  # noqa: E402
from text_utils import is_more_request as _real_is_more  # noqa: E402
from text_utils import normalize_markdown_tables as _real_normalize_markdown  # noqa: E402
# Handed through for the same reason as stem(): _capability_reply asks it
# which entity a "does X work with Y" question is about, and a lambda here
# would silently answer None, making every capability test pass vacuously.
from text_utils import capability_target as _real_capability_target  # noqa: E402

stub("text_utils",
     passes_retrieval_gate=lambda *a, **k: True,
     retrieval_confidence_band=lambda r, t, a: ("none" if not r else "confident"),
     is_refusal=lambda a: False, is_followup_turn=lambda *a: False,
     has_domain_vocabulary=lambda q: False, has_reference_markers=lambda q: False,
     is_template_leak=lambda a: False, build_clarification_options=lambda *a: [],
     stem=_real_stem, is_more_request=_real_is_more,
     capability_target=_real_capability_target,
     normalize_markdown_tables=_real_normalize_markdown)
stub("conversations", init_db=lambda: None, resolve_user_id=lambda u: None,
     save_turn=lambda *a, **k: None, list_conversations=lambda u: [],
     get_conversation=lambda *a: None, delete_conversation=lambda *a: True)

import main  # noqa: E402
app = main.app
