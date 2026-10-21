# GroundedOps: sheet for the IT reviewer

Written 2026-10-21 against product commit `4024b72`. DRAFT: not for sending until Dev's read-through has marked every sentence true (see appendix). If logging, providers or sign-in change, this sheet is out of date until redated.

GroundedOps answers support questions from your own manuals. This sheet says where your data lives, what leaves the machine, what is logged, and what we do not have. It claims nothing that is not in the product.

## What we do not have yet
- No certifications (no SOC 2, ISO 27001 or HIPAA).
- No uptime guarantee or SLA.
- No third-party penetration test report.
- One customer per installation. Two customers do not share one instance.

## 1. Where your documents live
| | Installed on your Windows machine | Docker on your server | Hosted by us |
|---|---|---|---|
| Original files | Folder on that machine (`documents`) | Docker volume / mounted `documents` folder | To be confirmed by GroundedOps before this sheet is sent |
| Search index | Folder on that machine (`chroma_db`) | Docker volume `grounded-data` | To be confirmed |
| Conversations, logs, accounts | Files and one SQLite file on that machine | Same, on the volume | To be confirmed |

Your documents are used only to answer questions on your instance. They are not used to train anything by us. [Dev to confirm no code path sends documents for training: the sheet says only what the code does, which is send question text and retrieved passages, see section 2.]

## 2. What runs where, and what leaves the machine
The search, ranking and answer-checking steps run on the machine. The model that writes the answer depends on the mode you choose.

| Mode | What writes the answer | What leaves the machine |
|---|---|---|
| Offline (Free) | Local models through Ollama on the same machine | Nothing for answering. Automatic retry on an outside provider is off by default. |
| Online | The provider you pick and key: DeepSeek, OpenAI or Anthropic (Claude) | The visitor's question and the manual passages used for the answer, to that provider, over HTTPS |
| Online through your own gateway | A model on your own network, through an OpenAI-compatible gateway you set (`OPENAI_BASE_URL`) | Nothing leaves your network; the gateway is yours |

Other outside connections, each only if you turn it on:
- Mail server you configure (SMTP): sign-in links and credit alerts.
- Microsoft (Entra ID) or Google Workspace: only when you set up work-account sign-in.
- Cloudflare tunnel: only if you use the Windows launcher's tunnel to publish the widget.
- First install downloads the search models once; set `HF_HUB_OFFLINE=1` afterwards to stop re-checking.

The widget tells visitors which providers may process their question, and how long questions are kept; the operator cannot edit that line.

## 3. What is public
- The chat widget and the endpoints it needs.
- Original documents the widget links to are downloadable by anyone who knows the file name. Documents you list as excluded are not served. If a manual must not be public, do not ingest it.
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

Who can read it: signed-in admin accounts. [Dev to fill in which level sees which screen.]

## 6. How a customer deletes their data
- A document: remove it in the console (Documents). [Dev to confirm whether the retained original file is also removed.]
- One conversation: delete it in the console.
- A staff account: delete it in the console.
- All visitor text: lower `RETENTION_DAYS`, or run `python retention.py` once.
- Everything: uninstall (delete the install folder), or remove the Docker volumes.

---

## Appendix for the reviewer only (code references; remove before sending)
| Sheet statement | Where it is true |
|---|---|
| Documents and index stay on the machine | `README.md` "Run with Docker" (volume `grounded-data`), `docker/docker-compose.yml` volumes; `CHROMA_DIR`, `SOURCE_FILE_DIR` in README config table; `routes_documents.py` `source_file` / `docstore.find` |
| Offline mode: local models, nothing out | `llm.py` `_chain_for` (api mode only uses providers; else `FALLBACK_CHAIN` is local), `README.md` "Local mode has no model fallback" |
| Escalation off by default in local mode | `llm.py` `escalation_role`, `keystore.local_escalation_allowed` |
| Online sends question and passages to the chosen provider | `llm.py` `_online_provider_model`, `outside_providers`; `widget_config.py` `ai_notice` |
| Gateway option | `llm.py` `OPENAI_BASE_URL` (`OPENAI_BASE`) |
| Provider list DeepSeek, OpenAI, Anthropic | `README.md` `ONLINE_PROVIDER` row |
| SMTP | `mailer.py`, `keystore.get_smtp_settings` |
| SSO, tenant and domain binding, no account created by sign-in | `sso.py` module docstring; `routes_admin_auth.py` `/admin/auth/sso/*` |
| Cloudflare tunnel | `README.md` (launcher note), `guards.py` `_PROXY_HEADERS` comment |
| First-run model download, `HF_HUB_OFFLINE` | `README.md` installer paragraph and config table |
| Widget notice not operator-editable | `widget_config.py` `ai_notice` docstring |
| Original files publicly fetchable by name; exclusions | `routes_documents.py` `source_file` docstring, `EXCLUDED_SOURCES` |
| Admin LAN-only, allowlist | `README.md` `ADMIN_ALLOWED_IPS`; `guards.py` `is_local_caller` |
| No TLS in the app | `README.md` `ENABLE_HSTS` row |
| Levels and names | `accounts.py` `LEVELS`; `docs/website-brief.md` section 0 |
| 12 hour sessions | `README.md` `SESSION_TTL_SECONDS` |
| Keys encrypted, limits of that | `keyvault.py` module docstring |
| What a log row holds, 10 MB rotation, 500 chars | `logger.py` `log_interaction`, `MAX_LOG_BYTES`, `MAX_ANSWER_LEN` |
| Console log redaction | `logger.py` `redact` |
| 90 days, 0 = off, stores pruned, what is not touched | `retention.py` docstring, `DEFAULT_DAYS`, `STORES`; each store's `prune_before` |
| Delete one conversation | `routes_settings.py` `conversations_delete` |
| Delete a document / account | `routes_documents.py` `/delete_source`; `routes_admin_auth.py` `DELETE /admin/users/{user_id}` |
| Single tenant | `README.md` Known limitations |
| No certifications | `docs/website-brief.md` section 0 "Do not invent" |

Known gap in our own docs: `README.md` Known limitations still says "No data-retention or PII policy", which is out of date since D-15 (`retention.py`). Dev to correct the README or this sheet will contradict it.
