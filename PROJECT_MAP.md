# GroundedOps — project map

Orientation for a new session working in this folder. Repo root:
`C:\Users\hrizvi\Downloads\Git\groundedops`, branch `main`.

**What it is:** retrieval-augmented Q&A with a verification layer — every answer is
checked against its retrieved sources before display, and the system refuses or asks
for clarification rather than guessing. FastAPI backend, React chat UI, an embeddable
single-file website widget, and a standalone HTML admin console. Runs Online (DeepSeek
/ OpenAI / Claude) or Free/offline (Ollama), switchable at runtime.

`README.md` (469 lines) is the full architecture doc — read it before changing behaviour.

---

## Full directory

Every directory on disk is listed. `.git/`, `.venv/` and `node_modules/` interiors are
collapsed; `__pycache__/` omitted.

```
groundedops/
├── .claude/                      Claude Code config for this project
├── .git/  .venv/
├── .obsidian/                    editor config for the notes vault, gitignored
├── GroundedOps-Vault/            Obsidian notes vault — NOT part of the app, gitignored
│   ├── README.md  GroundedOps.md  Architecture.md
│   ├── .obsidian/
│   ├── 10 Backend/               one .md per backend module (26 files) + Tooling.md
│   ├── 20 Frontend/
│   ├── 30 Infrastructure/
│   ├── 40 Concepts/
│   └── 50 API/                   API - admin.md
├── .github/
│   └── workflows/
│       └── ci.yml                unit+route tests → Docker image build → eval
│                                 schema selfcheck (advisory). Runs on push/PR to
│                                 main. Was sitting at repo root and never actually
│                                 ran — GitHub Actions only reads .github/workflows/.
├── .gitignore  LICENSE  Makefile
├── README.md                     architecture, components table, env vars, limitations
├── QUICKSTART.md                 double-click install path, no Docker
├── USER_GUIDE.md                 end-user guide to the running app
├── handover.txt                  live URLs + member token from the last run.cmd
│                                 (quick-tunnel URL rotates every restart), gitignored
├── install.cmd  install.ps1  install.sh        one-time setup: .venv, models, .env
├── run.cmd  run.ps1              the one launcher (start.cmd/start.ps1/start.sh/
│                                 start-lan.cmd were removed — strictly redundant
│                                 with flags here): rebuild frontend if stale,
│                                 ensure .env secrets, start backend on LAN, open a
│                                 Cloudflare quick tunnel, mint a member token, serve
│                                 the widget test harness, print the /admin URL.
│                                 -Local -NoTunnel -NoBuild -NoTestPage
├── docker/                       deployment stack — NOT for local dev, see below
│   ├── docker-compose.yml        backend + frontend + Ollama
│   ├── docker-compose.override.yml   DEV ONLY: bind-mounts src, uvicorn --reload,
│   │                             Vite HMR. Auto-merged by `docker compose` when
│   │                             run from this directory.
│   ├── Dockerfile.backend  Dockerfile.frontend
│   └── .dockerignore
├── conversations.db  quota.db                  runtime, gitignored
├── backend.log(.err)  tunnel.log(.err)  testpage.log(.err)   gitignored
├── corpus/                       source docs ingested by /ingest/reload_folder
│   ├── README.md
│   └── 7 vendor PDFs — MyCheckr User Manual v7, MyCheckr Mini v5, MyConnect
│       Environment v4, MyConnect Quick Start, MyCheckr install checklist,
│       Linux access guide, ICU_Network_API v1.0.50        gitignored
└── src/                          backend lives here; uvicorn runs with src as cwd
    ├── .claude/
    ├── .env                                    secrets, gitignored
    ├── main.py                   FastAPI app, every route, /query orchestration (2297)
    ├── db.py                     shared persistent ChromaDB client
    ├── parsing.py                page-preserving text extraction (PDF/DOCX/TXT)
    ├── chunking.py               step-boundary-aware chunking
    ├── ingest.py                 parse → chunk → breadcrumb → embed → store
    ├── embeddings.py             all-MiniLM-L6-v2 wrapper
    ├── retrieval_db.py           hybrid BM25 + dense, RRF-merged, scope-aware
    ├── reranker.py               sigmoid-calibrated cross-encoder
    ├── router.py                 semantic query classification
    │                             (extract / fast / accurate / reasoning)
    ├── structure.py              checklist / procedure extraction
    ├── llm.py                    Ollama + DeepSeek/OpenAI/Anthropic, condense_query,
    │                             rethink options, model warmup
    ├── grounding.py              NLI-based answer verification
    ├── memory.py                 session-scoped history, TTL-reaped
    ├── runtime_config.py         live online/offline toggle, process-wide
    ├── keyvault.py               Fernet-encrypted API key at rest
    ├── logger.py                 JSON interaction log
    ├── faq_store.py              curated Q/A pairs, candidate ranking, and the gap log
    │                             (v13.0's separate gaps.py was folded in here)
    ├── catalog.py                category / product catalog, source attachment
    ├── conversations.py          server-side conversation persistence
    ├── text_utils.py             pure-stdlib shared helpers, fully unit-tested
    ├── widget_api.py             public /widget/* router (v12.0), registered by main.py
    ├── quota.py                  tiers, per-caller quota, signed tokens
    ├── widget_config.py          widget branding + lead capture, imported by main.py —
    │                             GET/PUT /admin/widget/config, POST /widget/lead,
    │                             GET /admin/leads
    ├── mint_token.py             mint a widget token for testing
    ├── admin.html                standalone admin console, v13 "Nocturne" (1138)
    ├── nocturne/
    │   └── styles.css            the console's stylesheet, mounted at /nocturne
    ├── diagnose.py               ingest + retrieval pipeline check against live Chroma
    ├── diag_scope.py             document scope metadata report / --fix
    ├── _harness.py               imports main.py with ML modules stubbed, so the v13
    │                             routes can be tested without Chroma/Ollama
    ├── eval.py                   regression harness, gates changes on a baseline diff
    ├── eval_cases.json  eval_cases_16.json  eval_cases_api.json
    ├── eval_cases_extensive.json
    ├── eval_baseline.json  eval_baseline_16.json    committed on purpose (eval.py:11)
    ├── eval_results.json                             gitignored
    ├── run_tests.py              runs tests/ without pytest
    ├── test_queries.py           end-to-end smoke test against a running server
    ├── requirements.txt  BENCHMARKS.md
    ├── tests/
    │   ├── test_chunking.py  test_structure.py  test_text_utils.py  test_memory.py
    │   ├── test_router.py  test_rrf.py  test_llm.py  test_keyvault.py
    │   ├── test_regression_bugs.py    locks in fixes found via transcript analysis
    │   └── test_new_routes.py    FAQ/gaps/autogenerate route tests via _harness.py,
    │                             all passing — test_ui.py needs playwright, not installed
    ├── assets/
    │   ├── logo.svg  logo2.png  icon2.svg  groundedops-logo-animated.svg
    │   └── icons/  all.png folder.png key.png new.png reset.png
    │               theme_dark.png theme_light.png toggle.png  README.txt
    ├── chroma_db/                vector store, gitignored
    │   └── 8f903bd4-341e-464d-8c9d-3ec4833253f1/      the collection
    ├── corpus/                   second copy of the source docs
    ├── faq_store.json  faq_gaps.json  catalog_config.json
    ├── logs.json  logs.jsonl  conversations.db  quota.db        runtime, gitignored
    ├── frontend/                 React + Vite; the built dist is served by FastAPI
    │   ├── package.json  package-lock.json  vite.config.js  index.html
    │   ├── dev.mjs  README.md  nginx.conf  widget-nginx.conf  widget-test.html
    │   ├── node_modules/                             gitignored
    │   ├── dist/                 built output, gitignored (npm run build)
    │   │   ├── assets/  widget/
    │   ├── public/
    │   │   ├── logo.svg
    │   │   └── widget/
    │   │       ├── groundedops-widget.js     embeddable widget, no deps (967)
    │   │       └── groundedops-widget.php    WordPress plugin wrapper, untracked
    │   └── src/
    │       ├── main.jsx  App.jsx (661)  api.js  styles.css (521)
    │       ├── Logo.jsx  icons.jsx
    │       └── components/
    │           ├── ChatsPage.jsx  ChatFaqDrawer.jsx
    │           ├── DetailsPanel.jsx  Dialogs.jsx  Message.jsx  Rail.jsx
    │           └── FaqPage.jsx  FaqChoices.jsx    (FaqChoices is written but unwired)
    │           (AdminPanel.jsx removed — it duplicated admin.html/Nocturne and
    │            shipped admin code into the public chat bundle; its three
    │            trigger buttons now open /admin in a new tab instead)
    └── legacy/                   ~500 files. Reference only — nothing imports it.
        │                         Each release folder holds only that release's changed
        │                         files, plus files.zip and a CHANGELOG_*.md.
        ├── v3/                   flat, 21 files: app.py bm25.py chunking.py db.py
        │                         embeddings.py grounding.py ingest.py llm.py logger.py
        │                         main.py memory.py parsing.py reranker.py
        │                         retrieval_db.py router.py structure.py test.py
        │                         test_queries.py + deepseek key artifacts
        ├── v4/    v4.0  v4.1
        ├── v5/                   empty
        ├── v6/    v6.0/tests  v6.2  v6.3_changed_files/tests
        │          v6.4_changed_files/tests  v6.5_changed_files/tests
        ├── v7/    v7.0_changed_files/tests
        │          v7.1  v7.1.1–v7.1.4 (each with .streamlit/)
        │          v7.1.5–v7.1.7 (.streamlit/ + assets/icons/)
        │          v7.2_react_base/frontend/{public,src/components}   ← React starts here
        │          v7.2.1/frontend/…  v7.2.2/frontend/…
        ├── v8/    v8.0_general_improvements  v8.1_breadcrumbs
        │          v8.2_simultaneous_llms  v8.3
        │          v8.4/{8.4.1,8.4.2,8.4.3}
        ├── v9/    v9.0_doc2_query
        │          v9.1_online_default/{v9.1.1_bug_fix, v9.1.2_toggle_fix,
        │                               v9.1.3_force_pick, v9.1.4_chats, v9.1.5_chats2}
        │          v9.2_full_release_1.0/v9.2.1
        ├── v10/   v10.0_Docker_I_hardly_know  v10.1_multi_product  v10.2_ingest_fix
        │          v10.3_chat_fix/v10.3.1  v10.4_ci_cd  v10.5_ingest_cat  v10.6  v10.7
        │          v10.8_admin_panel  v10.9_FAQ  v10.10  v10.11  v10.12  v10.13
        │          v10.14_FAQ_ans/v10.14.1  v10.15_remove_ingest_faq
        │          v10.16_remove_obsolete/{v10.16.1, v10.16.2}
        ├── v11/   v11.1_widget
        │          v11.2_FAQ_match/{v11.2.1/files, v11.2.2}
        │          v11.3_FAQ_suggest/{v11.3.1, v11.3.2, v11.3.3}   ← current HEAD release
        ├── v12/   v12.0_Securityyy        quota.py + widget_api.py land here
        │          v12.1_cloud/{v12.1.1 … v12.1.5}   main.py 1842 ln, PHP plugin
        │          v12.2_admin_rebuild     admin.html + main.py 2077 ln
        │          v12.3_redesign/v12.3.1  admin.html, styles.css, main.py 2077 ln
        └── v13/   v13.0    the raw drop that caused BROKEN STATE. DEPLOY_v13.md,
                            admin.html, main.py, faq_store.py, gaps.py,
                            widget_config.py, styles.css, _harness.py,
                            test_new_routes.py, test_ui.py
                   v13.1    ← current release. Same v13.0 intent, actually merged
                            onto v12.3's main.py instead of replacing it — see
                            CHANGELOG_v13.1.md. main.py, faq_store.py, admin.html,
                            _harness.py, test_new_routes.py, files.zip
```

---

## Running it

```
install.cmd                              one-time
run.cmd                                  build + LAN + tunnel + token + test page +
                                         admin URL   → http://127.0.0.1:8000  (/admin)
run.cmd -Local -NoTunnel -NoTestPage    plain local run only
cd docker && docker compose up -d --build   deploy stack, app :8080, API :8000
                                         (NOT for local dev — see docker/docker-compose.yml)
python run_tests.py              unit + route tests, no pytest needed
pytest tests/                    if pytest is installed
python test_queries.py           e2e against a running server
python eval.py                   regression gate; --update-baseline to relock
```

Admin endpoints are gated by the `X-Admin-Password` header against `ADMIN_PASSWORD`
in `src/.env`. Full env var table is in `README.md`.

---

## State of the merge — RESOLVED

An earlier revision of this document recorded that the working-tree `main.py` and
`faq_store.py` were the v13.0 drop applied over an older base than HEAD, and had
therefore *replaced* committed features rather than adding to them. **That has since
been merged properly and the wiring completed.** History, so the same mistake isn't
re-made: `DEPLOY_v13.md` claims "additive: 324 lines added, 2 replaced", which was true
against its own base but not against HEAD — dropping those files in wholesale silently
reverted the CORS middleware, the `/api` prefix strip, `/source_file`, the SPA mount, and
`faq_store`'s entire v11.3.3 "ask, don't guess" candidate ranking.

Current state — `main.py` is 2250+ lines and carries **both** lineages:

- restored from HEAD: `CORSMiddleware`, the `/api` prefix-strip middleware, `_strip_meta`,
  `faq_id` / `skip_faq` on `QueryRequest`, `page_label` + `download_url` on sources,
  `GET /source_file/{filename}`, the `_SPAStatic` mount of `frontend/dist`
- kept from v13.0: the FAQ gap log (folded into `faq_store.py`, so `gaps.py` is gone),
  `POST /admin/faq/autogenerate` with `_parse_qa_json`, `GET /admin` serving the console
- the v12 public surface: `PUBLIC_ONLY` / `_PUBLIC_PREFIXES` gate, and `widget_api.py` +
  `quota.py` wired via `widget_api.register(app, _widget_answer)` at the bottom of the
  file, so `/widget/ask`, `/widget/faq`, `/widget/catalog` and `/widget/quota` are live
  with tiers and signed-token auth

`/source_file` is **not** on the open public allowlist by design — it is in
`_TOKEN_GATED_PREFIXES`, so externally it requires a valid member token, while LAN and
local callers pass through. Filenames appear in answers, so an open allowlist meant any
ingested document was downloadable by anyone who could guess one.

### Links wired in the follow-up pass

| Link | Fix |
|---|---|
| console → its stylesheet | `admin.html`'s first `<link>` is `nocturne/styles.css`, which the browser resolves to `/nocturne/styles.css`. Nothing served it, so the console rendered unstyled — the request fell through to the SPA mount, which re-raises for paths containing a dot. Now mounted from `src/nocturne/`, before the `/` mount, and off `_PUBLIC_PREFIXES` so it stays LAN-only with `/admin`. |
| console → widget design + leads | `widget_config.py` shipped with v13.0 but was never imported, so "Widget design", both contact forms and the leads list called routes that did not exist. Now imported, with `GET /widget/config`, `POST /widget/lead`, `GET/PUT /admin/widget/config`, `POST /admin/widget/config/reset`, `GET /admin/leads`, `POST /admin/leads/{id}/handled`, `DELETE /admin/leads/{id}`. |
| duplicate `/widget/config` | `widget_api.py` and the new v13 route both defined it; whichever registered first won silently. The route now lives only in `main.py`, and carries the two fields `widget_api`'s version supplied — `sign_in_url` and the caller's `quota` — so nothing was lost in the move. Quota is resolved defensively: a broken quota module degrades the upsell instead of breaking the config fetch the widget blocks on. |
| Vite dev proxy → backend | `vite.config.js` defaulted `API_TARGET` to `http://backend:8000`, a Compose service name that resolves only inside the Compose network, so the documented native `npm run dev` proxied every call into the void. Default is now `http://127.0.0.1:8000`; `docker-compose.override.yml` sets `API_TARGET` explicitly for the container. |
| `dev.mjs` → its own backend | `dev.mjs` chooses the backend port (`BACKEND_PORT`) but never told Vite, so `BACKEND_PORT=8001` started the backend on 8001 while Vite still proxied to 8000. It now passes `API_TARGET` to the Vite child. |

Verified by importing `main.py` through `_harness.py` (ML modules stubbed) and exercising
the routes with `TestClient`: every route above answers, `PUT /admin/widget/config`
persists, a lead round-trips through `POST /widget/lead` → `GET /admin/leads` → mark
handled → delete, `/nocturne/styles.css` and `/admin` both serve, `/widget/quota` answers,
and `/admin/widget/config` is 401 without the password.

**Not touched, deliberately:** there is no `POST /faq/gaps/{id}/resolve`. `admin.html`
only ever DELETEs a gap, and `faq_store` has no resolve-by-id — gaps auto-resolve through
`resolve_gap_matching` when an answer is written. Adding the route means adding a store
function, which is a feature, not a link fix.

### Known failing tests (pre-existing, not from this work)

`python run_tests.py` → **20/24 passed, 4 failed**. All four are `test_llm.py`'s
`test_fallback_chain_*`, failing with `fake_generate() got an unexpected keyword argument
'api_keys'`. Both `llm.py` and `tests/test_llm.py` are unmodified in the working tree, and
at HEAD `llm.py` already passes `api_keys` while the test's fake never accepted it — so
the fixture was committed stale. One-line fix (`**kw` on the fake), left alone because
loosening a test signature can mask a real argument-passing bug and nobody asked for it.

`tests/test_ui.py` needs `playwright`, which isn't installed; `pytest` isn't installed
either, so `run_tests.py` is the suite runner here.
