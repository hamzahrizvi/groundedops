import os
import time
import uuid
import requests
import streamlit as st

import keyvault

API_BASE = os.getenv("API_BASE", "http://localhost:8000")

st.set_page_config(page_title="GroundedOps", layout="wide")

# ── Light styling for the answer / rethink area ─────────────────────────
st.markdown(
    """
    <style>
      .answer-card { border:1px solid rgba(128,128,128,0.25); border-radius:12px;
        padding:16px 18px; margin:4px 0 10px 0; background:rgba(128,128,128,0.04); }
      .chip { display:inline-block; padding:2px 10px; border-radius:999px;
        font-size:0.72rem; font-weight:600; margin-right:6px; line-height:1.6; }
      .chip-grounded { background:rgba(46,160,67,0.15); color:#2ea043; }
      .chip-unverified { background:rgba(219,109,0,0.15); color:#db6d00; }
      .chip-provider { background:rgba(88,101,242,0.15); color:#5865f2; }
      .chip-clarify { background:rgba(0,120,212,0.12); color:#0078d4; }
      .chip-muted { background:rgba(128,128,128,0.15); color:#888; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("GroundedOps")


# ── Session state ────────────────────────────────────────────────────────

if "system_ready" not in st.session_state:
    st.session_state.system_ready = False
if "messages" not in st.session_state:
    st.session_state.messages = []
if "rethink_options" not in st.session_state:
    st.session_state.rethink_options = None
if "session_id" not in st.session_state:
    # One persistent id per browser session — sent with every /query so
    # the backend's conversation memory never mixes this conversation
    # with anyone else's (see memory.py).
    st.session_state.session_id = str(uuid.uuid4())

# One-time migration of any old plaintext key file into the encrypted
# vault, then forget it. Safe to call every launch.
keyvault.migrate_legacy_plaintext()


# ── Backend helpers ──────────────────────────────────────────────────────

def deepseek_key() -> str | None:
    """The stored key, decrypted on demand. Never held in session_state,
    never shown in the UI — only passed straight to the backend."""
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


# ── Sidebar ──────────────────────────────────────────────────────────────

with st.sidebar:
    st.subheader("DeepSeek Settings")

    key_is_set = keyvault.has_key()

    if key_is_set:
        st.success("🔒 Key saved (encrypted, hidden)")
        st.caption(
            "The key is stored encrypted on this machine and is never "
            "shown again. Enter a new one below to replace it."
        )
    else:
        st.info("No key set")

    # The field is ALWAYS empty — we never load the stored key back into
    # it, so the saved key can't be read off the screen. It exists only
    # to enter a new/replacement key.
    key_input = st.text_input(
        "Enter API key" + (" (replace existing)" if key_is_set else ""),
        value="",
        type="password",
        placeholder="sk-...",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Save Key"):
            if key_input.strip():
                keyvault.save_key(key_input.strip())
                st.success("Key saved (encrypted)")
                st.rerun()
            else:
                st.warning("Empty key")
    with c2:
        if st.button("Reset Key"):
            keyvault.clear_key()
            st.success("Key cleared")
            st.rerun()

    st.divider()

    if st.button("💬 New Conversation"):
        try:
            requests.post(f"{API_BASE}/clear_session",
                         json={"session_id": st.session_state.session_id}, timeout=15)
        except Exception:
            pass
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.messages = []
        st.rerun()

    if st.button("Reset Knowledge Base"):
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
        st.caption(f"Chunks: {stats.get('total_chunks', 0)}")

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

st.success("System ready — models loaded")


# ── Upload ───────────────────────────────────────────────────────────────

st.subheader("Upload documents")
uploaded_files = st.file_uploader(
    "Upload .txt / .pdf / .docx files",
    accept_multiple_files=True,
    type=["txt", "pdf", "docx"],
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

st.divider()
st.subheader("Conversation")


# ── Answer status chips ─────────────────────────────────────────────────

def render_status_chips(meta: dict) -> None:
    """Compact status row: grounding state, provider/model, escalation."""
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
        chips.append(f'<span class="chip chip-grounded">grounded {grounding:.2f}</span>')
    elif grounding is None and role not in ("clarify", "rejected"):
        chips.append('<span class="chip chip-grounded">grounded</span>')

    if provider and model and provider != "none":
        label = f"{provider}/{model}"
        chips.append(f'<span class="chip chip-provider">{label}</span>')

    if meta.get("escalated_to_deepseek"):
        chips.append('<span class="chip chip-provider">auto-retried on DeepSeek</span>')
    elif meta.get("fallback_used"):
        chips.append('<span class="chip chip-muted">fallback used</span>')

    if chips:
        st.markdown(" ".join(chips), unsafe_allow_html=True)


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
                        "Ask more about this document",
                        key=f"ask_more_{msg_idx}_{i}",
                        placeholder="e.g. what else does this section say?",
                    )
                    if ask_more:
                        st.session_state.pending_query = ask_more
                        st.session_state.pending_source_filter = src["source"]
                        st.rerun()


# ── Redesigned rethink control ──────────────────────────────────────────

def render_rethink(original_query: str, msg_idx: int, current_meta: dict) -> None:
    """A prominent 'answer again with a specific model' control.

    Local models are always available. DeepSeek is shown but disabled
    with an inline hint when no key is set. The model that produced the
    current answer is marked so it's clear what you're comparing against.
    """
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

    with st.expander("🔁 Rethink this answer with another model"):
        labels = [describe(o) for o in options]
        selected_label = st.radio(
            "Choose a model to re-answer with:",
            labels,
            key=f"rethink_radio_{msg_idx}",
            index=0,
        )
        chosen = options[labels.index(selected_label)]

        needs_key = chosen["provider"] == "deepseek" and not have_key
        if needs_key:
            st.warning("Set a DeepSeek API key in the sidebar to use this model.")

        if st.button("Re-answer", key=f"rethink_btn_{msg_idx}", disabled=needs_key):
            with st.spinner(f"Re-answering with {chosen['provider']}/{chosen['model']}..."):
                data = run_query(
                    original_query,
                    force_provider=chosen["provider"],
                    force_model=chosen["model"],
                )
            if data:
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": data.get("answer", ""),
                    "meta": data,
                    "query": original_query,
                })
                st.rerun()


def render_meta_expander(meta: dict) -> None:
    details = {
        "role": meta.get("role"),
        "model": meta.get("model"),
        "provider": meta.get("provider"),
        "fallback_used": meta.get("fallback_used"),
        "escalated_to_deepseek": meta.get("escalated_to_deepseek"),
        "grounding_score": meta.get("grounding_score"),
        "flagged": meta.get("flagged"),
        "retrieval_score": meta.get("retrieval_score"),
        "reason": meta.get("reason"),
        "timing": meta.get("timing"),
    }
    if meta.get("resolved_query"):
        details["searched_for"] = meta["resolved_query"]
    with st.expander("Details"):
        st.json(details)


def render_assistant_block(meta: dict, content: str, msg_idx: int, query: str | None) -> None:
    """The full assistant turn: answer + chips + clarify hint + sources +
    rethink + raw details."""
    st.markdown(content)
    render_status_chips(meta)

    if meta.get("role") == "clarify":
        st.info("I need a bit more detail to answer this — see the question above.")

    if meta.get("resolved_query"):
        st.caption(f"🔎 Searched for: _{meta['resolved_query']}_")

    render_sources(meta.get("sources", []), msg_idx)

    # Rethink is offered whenever we produced a real model answer to a
    # real query (not for clarify/rejected turns, which have nothing to
    # re-answer).
    if query and meta.get("role") not in ("clarify", "rejected"):
        render_rethink(query, msg_idx, meta)

    render_meta_expander(meta)


# ── Render conversation history ──────────────────────────────────────────

for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant" and msg.get("meta"):
            render_assistant_block(msg["meta"], msg["content"], idx, msg.get("query"))
        else:
            st.markdown(msg["content"])


# ── Chat input (handles both normal typing and "ask more" clicks) ───────

pending = st.session_state.pop("pending_query", None) if "pending_query" in st.session_state else None
pending_filter = st.session_state.pop("pending_source_filter", None) if "pending_source_filter" in st.session_state else None

user_query = st.chat_input("Ask a question") or pending

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
                "role": "assistant",
                "content": data.get("answer", ""),
                "meta": data,
                "query": user_query,
            })
