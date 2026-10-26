# GroundedOps: sheet for the IT reviewer

Written 2026-10-21 against product commit `2491a01`. If logging, providers or sign-in change, this sheet is out of date until redated.

GroundedOps answers support questions from your own manuals. This sheet says where your data lives, what leaves the machine, what is logged, and what we do not have. It claims nothing that is not in the product.

## What we do not have yet
- No certifications (no SOC 2, ISO 27001 or HIPAA).
- No uptime guarantee or SLA.
- No third-party penetration test report.
- One customer per installation. Two customers do not share one instance.

## 1. Where your documents live
| | Installed on your Windows machine | Docker on your server |
|---|---|---|
| Original files | Folder on that machine (`documents`) | Mounted `documents` folder |
| Search index | Folder on that machine (`chroma_db`) | Docker volume `grounded-data` |
| Conversations, logs, accounts | Files and SQLite databases on that machine | Same, on the volume |

Your documents are used only to answer questions and to run the console's tools on your instance. Nothing in the product sends your documents, questions or logs to GroundedOps.

## 2. What runs where, and what leaves the machine
Search, ranking, text recognition (OCR) of scanned pages and the first answer check run on the machine. Every other model step (writing the answer, a second check of an answer the first could not verify, rewording a follow-up question, asking back on a vague question, handling a question in another language) goes to the model the mode uses.

| Mode | What writes the answer | What leaves the machine |
|---|---|---|
| Offline (Free) | Local models through Ollama on the same machine | Nothing for answering. Automatic retry on an outside provider is off by default. |
| Online | The provider you pick and key: DeepSeek, OpenAI or Anthropic (Claude) | The visitor's question, earlier turns of the same conversation and the manual passages used for the answer, to that provider, over HTTPS |
| Online through your own gateway | A model on your own network, through an OpenAI-compatible gateway you set (`OPENAI_BASE_URL`) | Nothing leaves your network; the gateway is yours |

Console tools an Admin starts also use a model, and in any mode they can name an outside provider that has a key stored: drafting FAQ entries from a document sends up to 8,000 characters of that document; importing an existing FAQ page sends up to 12,000 characters of it; re-answering a question with another model sends the question and its passages.

Other outside connections, each only if you set it up:
- Mail server you configure (SMTP): sign-in links, credit alerts, and enquiry forms visitors send from the widget.
- Microsoft (Entra ID) or Google Workspace: only when you set up work-account sign-in.
- Cloudflare tunnel: the Windows launcher (`run.cmd`) opens one by default to publish the widget; `run.cmd -NoTunnel` does not.
- A Windows or source install downloads the local search models once, on its first run, each at a pinned version; set `HF_HUB_OFFLINE=1` afterwards to stop re-checking. The Docker image carries these models inside and does not download them. A source or Docker install still fetches the OCR model the first time a scanned page is read.

The widget tells visitors which providers may process their question, and how long questions are kept; the operator cannot edit that line.

## 3. What is public
- The chat widget and the endpoints it needs.
- Original documents and the figures cut from them are served to a caller that comes through a proxy or tunnel only with a valid widget member token. A caller that reaches the server directly, without a proxy, is not asked for a token: if the server itself listens on an address outside your network, anyone who can reach that address can download a retained document by its file name. Documents you list as excluded are never served.
- The admin console is limited to your local network by default. Opening it to other addresses is an explicit allowlist (`ADMIN_ALLOWED_IPS`), and sign-in is still required.
- The app does not serve TLS itself; put it behind your own HTTPS proxy.

## 4. Who can sign in
- Admin accounts are created or approved by an Owner; signing in with Microsoft or Google does not create an account.
- Microsoft sign-in must be tied to your own tenant; Google can be restricted to your Workspace domain.
- Three levels exist: Viewer, Admin, Owner (`basic`, `support`, `root` internally).
- Sessions last 12 hours by default.
- API keys are stored encrypted on the machine. The key to open them is also on that machine, so this stops casual exposure, not someone who already controls the machine.

## 5. What is logged, for how long, who can read it
Logged for each question: the question, the first 500 characters of the answer, the model used, the sources cited, the grounding score, a session id, and how the turn ended. Stored in the machine's log file (rotated at 10 MB), the conversation database, widget feedback, enquiry forms and the unanswered-question list. The process console log records only a length and a short hash of visitor text, never the text.

Retention: visitor text is deleted after 90 days by default (`RETENTION_DAYS`; 0 turns deletion off). Deleted: logs, conversations, feedback votes, unsent enquiries, unanswered-question list. Never deleted by this job: your documents, curated FAQ, the index, staff accounts, quota counts.

Who can read it: Admin and Owner accounts. Every console screen that shows visitor text (the report, unanswered questions, enquiries, conversations) and the console backup, which includes them, needs Admin or above. A Viewer account can sign in and change its own password, and nothing else. Anyone with access to the machine's files can read the stores directly.

## 6. How a customer deletes their data
- A document: remove it in the console (Documents). This removes it from the index and deletes the figures cut from it. The original file stays in the `documents` folder, and can still be downloaded as described in section 3, until you delete that file yourself.
- A single conversation: there is no console screen for this; conversations go with the rest of the visitor text under retention.
- A staff account: an Owner deletes it in the console.
- All visitor text: lower `RETENTION_DAYS`, or run `python retention.py` once.
- Everything: uninstall (delete the install folder), or remove the Docker volumes.

---

## Appendix for the reviewer only (code references; remove before sending)
| Sheet statement | Where it is true |
|---|---|
| Documents and index stay on the machine | `docstore.py` `store_dir` (`documents`, `SOURCE_FILE_DIR`); `docker/docker-compose.yml` volume `grounded-data`, `../documents:/data/documents`; `CHROMA_DIR` in README config table |
| SQLite and file stores | `conversations.py` `DB_PATH`, `quota.py` (sqlite3), `accounts.py` `_PATH`, `logger.py` `LOG_FILE` |
| Nothing sent to GroundedOps | Outbound hosts in `src/*.py`: provider APIs (`llm.py`), `credit_watch.py` balance checks, `sso.py` Microsoft/Google, Ollama; none of ours |
| OCR runs locally | `ocr.py` (RapidOCR, ONNX, local) |
| Second check, condense, language use the mode's model | `main.py` `_llm_verified` (`generate_with_fallback`); `llm.py` `condense_query`; `language.py` `_generate`; `clarify.py` (`generate_with_fallback("fast")`) |
| Offline mode: local models, nothing out | `llm.py` `_chain_for` (api mode only uses providers; else `FALLBACK_CHAIN` is local), `README.md` "Local mode has no model fallback" |
| Escalation off by default in local mode | `llm.py` `escalation_role`, `keystore.local_escalation_allowed` |
| Online sends question, history and passages to the chosen provider | `llm.py` `_online_provider_model`, `outside_providers`, `condense_query`; `main.py` `build_answer_prompt`; `widget_config.py` `ai_notice` |
| Console tools can name a provider | `routes_faq.py` `admin_faq_autogenerate` / `admin_faq_draft_stream` (`_draft_sample`: 12 chunks, 8000 chars), FAQ import prompt (`text[:12000]`); `main.py` rethink branch (`force_provider`); `llm.generate` |
| Gateway option | `llm.py` `OPENAI_BASE_URL` (`OPENAI_BASE`) |
| Provider list DeepSeek, OpenAI, Anthropic | `README.md` `ONLINE_PROVIDER` row; `llm.py` `generate` |
| SMTP: sign-in links, credit alerts, enquiries | `mailer.py`; callers `routes_admin_auth.py`, `credit_watch.py`, `routes_widget.py` (enquiry email) |
| SSO, tenant and domain binding, no account created by sign-in | `sso.py` module docstring; `routes_admin_auth.py` `/admin/auth/sso/*` |
| Cloudflare tunnel on by default in the launcher | `README.md` "Native install" (`run.cmd`, `-NoTunnel`) |
| First-run model download, `HF_HUB_OFFLINE`, OCR model | `model_pins.py` `REVISIONS`; `docker/Dockerfile.backend` (models baked in, `HF_HUB_OFFLINE=1`); `README.md` installer paragraph and config table; `ocr.py` docstring |
| Widget notice not operator-editable | `widget_config.py` `ai_notice` docstring |
| Originals and figures token-gated except for header-less callers from a private address; exclusions | `main.py` `_TOKEN_GATED_PREFIXES`, `_restrict_private_surface`; `routes_documents.py` `source_file`, `EXCLUDED_SOURCES` |
| Admin LAN-only, allowlist | `README.md` `ADMIN_ALLOWED_IPS`; `main.py` `_restrict_private_surface`, `_admin_ip_allowed` |
| No TLS in the app | `README.md` `ENABLE_HSTS` row |
| Levels and names | `accounts.py` `LEVELS`; `docs/website-brief.md` section 0 |
| 12 hour sessions | `accounts.py` `SESSION_TTL_SECONDS`; `README.md` |
| Keys encrypted, limits of that | `keyvault.py` module docstring |
| What a log row holds, 10 MB rotation, 500 chars | `logger.py` `log_interaction`, `_trace_fields`, `MAX_LOG_BYTES`, `MAX_ANSWER_LEN` |
| Console log redaction | `logger.py` `redact` |
| 90 days, 0 = off, stores pruned, what is not touched | `retention.py` docstring, `DEFAULT_DAYS`, `STORES`; each store's `prune_before`; `README.md` Known limitations |
| Who reads visitor text | `guards.py` `_require_admin` (support and up) on `/admin/report`, `/faq/gaps`, `/admin/leads`, `/conversations`, `/admin/backup/export`; `_require_session` alone only on `/admin/auth/me` and `/admin/auth/password` |
| Deleting a document keeps the original | `routes_documents.py` `remove_source` (index + `figures.remove_figures` only); `docstore.py` has no delete; `source_file` serves via `docstore.find` |
| No single-conversation delete screen | `admin.html` has no conversation view; `routes_settings.py` `conversations_delete` is an API call needing the visitor's user id |
| Delete a staff account | `routes_admin_auth.py` `DELETE /admin/users/{user_id}` (`_require_root`) |
| Single tenant | `README.md` Known limitations |
| No certifications | `docs/website-brief.md` section 0 "Do not invent" |
