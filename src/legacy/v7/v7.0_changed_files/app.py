import os
import time
import uuid
import requests
import streamlit as st

import keyvault

API_BASE = os.getenv("API_BASE", "http://localhost:8000")

st.set_page_config(page_title="GroundedOps", layout="wide", page_icon="◆")

# ── Styling: minimal, high-end, dark-friendly ───────────────────────────
st.markdown(
    """
    <style>
      :root {
        --go-accent:#6C8CFF; --go-accent2:#9C6CFF;
        --go-green:#2ea043; --go-amber:#db6d00; --go-muted:#8a8f98;
      }
      .block-container { padding-top: 2.2rem; max-width: 900px; }
      /* Hero header */
      .go-hero { display:flex; align-items:center; gap:14px; margin-bottom:2px; }
      .go-logo { width:38px; height:38px; flex:0 0 auto; }
      .go-title { font-size:1.7rem; font-weight:700; letter-spacing:-0.02em;
        background:linear-gradient(92deg,var(--go-accent),var(--go-accent2));
        -webkit-background-clip:text; background-clip:text;
        -webkit-text-fill-color:transparent; line-height:1.1; }
      .go-sub { color:var(--go-muted); font-size:0.86rem; margin:0 0 4px 52px; }
      /* Answer card */
      .answer-card { border:1px solid rgba(128,128,128,0.22); border-radius:14px;
        padding:2px 2px; margin:2px 0 8px 0; }
      /* Chips */
      .chip { display:inline-block; padding:3px 11px; border-radius:999px;
        font-size:0.72rem; font-weight:600; margin:2px 6px 2px 0; line-height:1.7; }
      .chip-grounded { background:rgba(46,160,67,0.15); color:var(--go-green); }
      .chip-unverified { background:rgba(219,109,0,0.15); color:var(--go-amber); }
      .chip-provider { background:rgba(108,140,255,0.16); color:var(--go-accent); }
      .chip-clarify { background:rgba(156,108,255,0.16); color:var(--go-accent2); }
      .chip-muted { background:rgba(138,143,152,0.16); color:var(--go-muted); }
      /* Status dot in sidebar */
      .go-dot { height:9px; width:9px; border-radius:50%; display:inline-block;
        margin-right:7px; vertical-align:middle; }
      .go-dot-on { background:var(--go-green); box-shadow:0 0 8px var(--go-green); }
      .go-dot-off { background:var(--go-muted); }
      div[data-testid="stChatInput"] textarea { border-radius:12px; }
    </style>
    """,
    unsafe_allow_html=True,
)

_LOGO_SVG = """
<svg class="go-logo" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="48" y2="48">
      <stop offset="0" stop-color="#6C8CFF"/><stop offset="1" stop-color="#9C6CFF"/>
    </linearGradient>
  </defs>
  <rect x="3" y="3" width="42" height="42" rx="12" stroke="url(#g)" stroke-width="2.4"/>
  <circle cx="24" cy="24" r="6.5" fill="url(#g)"/>
  <path d="M24 6 V13 M24 35 V42 M6 24 H13 M35 24 H42" stroke="url(#g)" stroke-width="2.4" stroke-linecap="round"/>
</svg>
"""

st.markdown(
    f'<div class="go-hero">{_LOGO_SVG}<span class="go-title">GroundedOps</span></div>'
    '<p class="go-sub">Answers grounded in your documents — with sources, and nothing invented.</p>',
    unsafe_allow_html=True,
)


# ── Session state ────────────────────────────────────────────────────────

if "system_ready" not in st.session_state:
    st.session_state.system_ready = False
if "messages" not in st.session_state:
    st.session_state.messages = []
if "rethink_options" not in st.session_state:
    st.session_state.rethink_options = None
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

keyvault.migrate_legacy_plaintext()


# ── Backend helpers ──────────────────────────────────────────────────────

def deepseek_key() -> str | None:
    """Decrypt-on-demand. Never held in session_state, never shown."""
    return keyvault.load_key()


def run_query(user_query: str, force_provider: str | None = None, force_model: str | None = None,
              source_filter: str | None = None) -> dict | None:
    payload = {
        "q": user_query,
        "session_id": st.session_state.session_id,
        "deepseek_api_key": deepseek_key(),
        "force_provider": force_provider,
        "force_model": force_model,
        "source_filter": source_filter,
    }
    res = requests.post(f"{API_BASE}/query", json=payload, timeout=300)
    if not res.ok:
        st.error(res.text)
        return None
    return res.json()


def fetch_source_chunks(chunk_ids: list[str]) -> list[dict]:
    try:
        res = requests.post(f"{API_BASE}/source_chunks", json={"chunk_ids": chunk_ids}, timeout=30)
        if res.ok:
            return res.json().get("chunks", [])
    except Exception as e:
        st.error(f"Could not load source: {e}")
    return []


def fetch_rethink_options() -> list[dict]:
    if st.session_state.rethink_options is None:
        try:
            res = requests.get(f"{API_BASE}/rethink_options", timeout=10)
            st.session_state.rethink_options = res.json().get("options", []) if res.ok else []
        except Exception:
            st.session_state.rethink_options = []
    return st.session_state.rethink_options


def submit_query(text: str, source_filter: str | None = None):
    """Queue a query to run on the next rerun (used by dropdowns/buttons)."""
    st.session_state.pending_query = text
    if source_filter:
        st.session_state.pending_source_filter = source_filter
    st.rerun()


# ── Sidebar ──────────────────────────────────────────────────────────────

with st.sidebar:
    st.subheader("DeepSeek")

    key_is_set = keyvault.has_key()
    dot = "go-dot-on" if key_is_set else "go-dot-off"
    label = "Key saved (encrypted, hidden)" if key_is_set else "No key set"
    st.markdown(f'<span class="go-dot {dot}"></span>{label}', unsafe_allow_html=True)

    # A FORM with clear_on_submit=True is the correct fix for the
    # "typed key stays visible until refresh" bug: Streamlit's text_input
    # value= is only a first-render default and is ignored once the user
    # types, so st.rerun() alone won't clear it. clear_on_submit wipes the
    # field the instant the form is submitted — the key is encrypted and
    # the field is emptied in the same action, so it's never left on screen.
    with st.form("deepseek_key_form", clear_on_submit=True):
        key_input = st.text_input(
            "Enter API key" + (" (replaces existing)" if key_is_set else ""),
            value="", type="password", placeholder="sk-...",
        )
        c1, c2 = st.columns(2)
        save_clicked = c1.form_submit_button("Save", use_container_width=True)
        clear_clicked = c2.form_submit_button("Clear", use_container_width=True)

    if save_clicked:
        if key_input.strip():
            keyvault.save_key(key_input.strip())
            st.success("Saved & encrypted")
            st.rerun()
        else:
            st.warning("Empty key")
    if clear_clicked:
        keyvault.clear_key()
        st.success("Key cleared")
        st.rerun()

    if key_is_set:
        st.caption("Stored encrypted on this machine. Never displayed again.")

    st.divider()

    if st.button("💬 New conversation", use_container_width=True):
        try:
            requests.post(f"{API_BASE}/clear_session",
                         json={"session_id": st.session_state.session_id}, timeout=15)
        except Exception:
            pass
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.rerun()

    if st.button("♻️ Reset knowledge base", use_container_width=True):
        try:
            requests.post(f"{API_BASE}/reset", timeout=30)
            st.session_state.messages = []
            st.session_state.session_id = str(uuid.uuid4())
            st.success("Knowledge base reset")
            st.rerun()
        except Exception as e:
            st.error(f"Reset failed: {e}")

    st.divider()
    st.subheader("Documents")

    try:
        stats = requests.get(f"{API_BASE}/stats", timeout=10).json()
        sources = stats.get("sources", [])
        st.caption(f"{len(sources)} document(s) · {stats.get('total_chunks', 0)} chunks")

        if not sources:
            st.info("No documents uploaded")
        else:
            for src in sources:
                row1, row2 = st.columns([4, 1])
                row1.caption(src)
                if row2.button("✕", key=f"del_{src}"):
                    res = requests.post(f"{API_BASE}/delete_source", json={"source": src}, timeout=30)
                    if res.ok:
                        st.session_state.messages = []
                        st.success(f"Removed: {src}")
                        st.rerun()
                    else:
                        st.error(res.text)
    except Exception as e:
        st.warning(f"Could not load documents: {e}")


# ── Wait for backend readiness ──────────────────────────────────────────

if not st.session_state.system_ready:
    st.subheader("Loading system")
    progress_bar = st.progress(0)
    status_box = st.empty()
    error_box = st.empty()

    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            res = requests.get(f"{API_BASE}/status", timeout=5)
            data = res.json()
            progress_bar.progress(int(data.get("progress", 0)))
            status_box.info(data.get("message", "Starting..."))
            if data.get("error"):
                error_box.warning(f"Startup issue: {data['error']} — continuing with limited functionality")
                st.session_state.system_ready = True
                st.rerun()
            if data.get("ready"):
                st.session_state.system_ready = True
                st.rerun()
        except Exception:
            status_box.warning("Waiting for backend...")
        time.sleep(1)

    st.error("System did not become ready in time.")
    st.stop()


# ── Upload ───────────────────────────────────────────────────────────────

with st.expander("📄 Upload documents (.txt / .pdf / .docx)"):
    uploaded_files = st.file_uploader(
        "Drop files here", accept_multiple_files=True, type=["txt", "pdf", "docx"],
        label_visibility="collapsed",
    )
    if uploaded_files:
        for f in uploaded_files:
            with st.spinner(f"Uploading {f.name}..."):
                try:
                    res = requests.post(
                        f"{API_BASE}/upload",
                        files={"file": (f.name, f.getvalue(), f.type or "application/octet-stream")},
                        timeout=300,
                    )
                    st.write(res.json() if res.ok else res.text)
                except Exception as e:
                    st.error(f"Upload failed for {f.name}: {e}")


# ── Status chips ─────────────────────────────────────────────────────────

def render_status_chips(meta: dict) -> None:
    role = meta.get("role")
    provider = meta.get("provider") or ""
    model = meta.get("model") or ""
    grounding = meta.get("grounding_score")
    flagged = meta.get("flagged")

    chips = []
    if role == "clarify":
        chips.append('<span class="chip chip-clarify">needs clarification</span>')
    elif role == "rejected":
        chips.append('<span class="chip chip-muted">not in knowledge base</span>')
    elif flagged:
        chips.append('<span class="chip chip-unverified">unverified</span>')
    elif grounding is not None:
        chips.append(f'<span class="chip chip-grounded">grounded · {grounding:.2f}</span>')
    elif role not in ("clarify", "rejected"):
        chips.append('<span class="chip chip-grounded">grounded</span>')

    if provider and model and provider != "none":
        chips.append(f'<span class="chip chip-provider">{provider}/{model}</span>')
    if meta.get("escalated_to_deepseek"):
        chips.append('<span class="chip chip-provider">auto-retried on DeepSeek</span>')
    elif meta.get("fallback_used"):
        chips.append('<span class="chip chip-muted">fallback used</span>')

    if chips:
        st.markdown(" ".join(chips), unsafe_allow_html=True)


# ── Disambiguation dropdown for clarify turns ───────────────────────────

def render_clarification_picker(meta: dict, msg_idx: int) -> None:
    """When the backend returns clarification_options, offer them as a
    dropdown with 'Other (type it)' and 'Skip', so the user can pin the
    intended context in one click instead of re-typing the whole thing."""
    options = meta.get("clarification_options") or []
    if not options:
        return

    OTHER = "✏️ Other (type it myself)"
    SKIP = "✕ Skip"
    reason = meta.get("reason", "")
    prompt = ("Which of these did you mean?"
              if reason == "low_retrieval_confidence_followup"
              else "Which device or area are you asking about?")

    choice = st.selectbox(
        prompt, options + [OTHER, SKIP],
        key=f"clarify_select_{msg_idx}", index=0,
    )

    if choice == SKIP:
        return
    if choice == OTHER:
        typed = st.text_input("Type what you meant:", key=f"clarify_other_{msg_idx}",
                              placeholder="e.g. how do I factory-reset the MyCheckr Mini?")
        if st.button("Ask", key=f"clarify_other_btn_{msg_idx}") and typed.strip():
            submit_query(typed.strip())
        return

    # A concrete option was chosen — build a refined query from it.
    if st.button("Ask about this", key=f"clarify_pick_btn_{msg_idx}"):
        if reason == "low_retrieval_confidence_followup":
            refined = choice  # re-ask the chosen prior topic directly
        else:
            refined = f"{choice}: {meta.get('resolved_query') or ''}".strip().rstrip(":")
            if not meta.get("resolved_query"):
                refined = choice
        submit_query(refined)


# ── Sources ──────────────────────────────────────────────────────────────

def render_sources(sources: list, msg_idx: int) -> None:
    if not sources:
        return
    st.caption("Sources (click to view what was retrieved):")
    cols = st.columns(min(len(sources), 4))
    for i, src in enumerate(sources):
        col = cols[i % len(cols)]
        with col:
            label = src["source"][:24] + ("…" if len(src["source"]) > 24 else "")
            if st.button(f"📄 {label}", key=f"src_{msg_idx}_{i}"):
                st.session_state[f"show_src_{msg_idx}_{i}"] = not st.session_state.get(f"show_src_{msg_idx}_{i}", False)
            if st.session_state.get(f"show_src_{msg_idx}_{i}"):
                chunks = fetch_source_chunks(src.get("chunk_ids", []))
                with st.expander(f"Content from {src['source']}", expanded=True):
                    for c in chunks:
                        st.markdown(f"> {c['text']}")
                    ask_more = st.text_input(
                        "Ask more about this document", key=f"ask_more_{msg_idx}_{i}",
                        placeholder="e.g. what else does this section say?",
                    )
                    if ask_more:
                        submit_query(ask_more, source_filter=src["source"])


# ── Rethink control ──────────────────────────────────────────────────────

def render_rethink(original_query: str, msg_idx: int, current_meta: dict) -> None:
    options = fetch_rethink_options()
    if not options:
        return
    have_key = keyvault.has_key()
    current = f"{current_meta.get('provider')}/{current_meta.get('model')}"

    def describe(o: dict) -> str:
        base = f"{o['provider']}/{o['model']}"
        tags = []
        if base == current:
            tags.append("current")
        if o["provider"] == "deepseek" and not have_key:
            tags.append("needs key")
        return base + (f"  ·  {', '.join(tags)}" if tags else "")

    with st.expander("🔁 Rethink with another model"):
        labels = [describe(o) for o in options]
        selected_label = st.radio("Re-answer with:", labels, key=f"rethink_radio_{msg_idx}", index=0)
        chosen = options[labels.index(selected_label)]
        needs_key = chosen["provider"] == "deepseek" and not have_key
        if needs_key:
            st.warning("Set a DeepSeek API key in the sidebar to use this model.")
        if st.button("Re-answer", key=f"rethink_btn_{msg_idx}", disabled=needs_key):
            with st.spinner(f"Re-answering with {chosen['provider']}/{chosen['model']}..."):
                data = run_query(original_query, force_provider=chosen["provider"], force_model=chosen["model"])
            if data:
                st.session_state.messages.append({
                    "role": "assistant", "content": data.get("answer", ""),
                    "meta": data, "query": original_query,
                })
                st.rerun()


def render_meta_expander(meta: dict) -> None:
    details = {
        "role": meta.get("role"), "model": meta.get("model"), "provider": meta.get("provider"),
        "fallback_used": meta.get("fallback_used"), "escalated_to_deepseek": meta.get("escalated_to_deepseek"),
        "grounding_score": meta.get("grounding_score"), "flagged": meta.get("flagged"),
        "retrieval_score": meta.get("retrieval_score"), "reason": meta.get("reason"),
        "timing": meta.get("timing"),
    }
    if meta.get("resolved_query"):
        details["searched_for"] = meta["resolved_query"]
    with st.expander("Details"):
        st.json(details)


def render_assistant_block(meta: dict, content: str, msg_idx: int, query: str | None) -> None:
    st.markdown(content)
    render_status_chips(meta)

    if meta.get("role") == "clarify":
        render_clarification_picker(meta, msg_idx)

    if meta.get("resolved_query"):
        st.caption(f"🔎 Searched for: _{meta['resolved_query']}_")

    render_sources(meta.get("sources", []), msg_idx)

    if query and meta.get("role") not in ("clarify", "rejected"):
        render_rethink(query, msg_idx, meta)

    render_meta_expander(meta)


# ── Conversation ─────────────────────────────────────────────────────────

st.divider()

for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant" and msg.get("meta"):
            render_assistant_block(msg["meta"], msg["content"], idx, msg.get("query"))
        else:
            st.markdown(msg["content"])


# ── Chat input (handles typing, "ask more", and clarify picks) ──────────

pending = st.session_state.pop("pending_query", None) if "pending_query" in st.session_state else None
pending_filter = st.session_state.pop("pending_source_filter", None) if "pending_source_filter" in st.session_state else None

user_query = st.chat_input("Ask a question about your documents") or pending

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                data = run_query(user_query, source_filter=pending_filter)
            except Exception as e:
                st.error(f"API error: {e}")
                data = None
        if data:
            new_idx = len(st.session_state.messages)
            render_assistant_block(data, data.get("answer", ""), new_idx, user_query)
            st.session_state.messages.append({
                "role": "assistant", "content": data.get("answer", ""),
                "meta": data, "query": user_query,
            })
