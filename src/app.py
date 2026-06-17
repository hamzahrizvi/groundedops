import json
import os
import time
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://localhost:8000")
KEY_FILE = ".deepseek_key.json"

st.set_page_config(page_title="GroundedOps", layout="wide")
st.title("GroundedOps")


# ── Key management ──────────────────────────────────────────────────────

def load_saved_key() -> str:
    if os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("key", "")
        except Exception:
            return ""
    return ""


def save_key(key: str) -> None:
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        json.dump({"key": key}, f)


def clear_key() -> None:
    if os.path.exists(KEY_FILE):
        os.remove(KEY_FILE)


# ── Session state ────────────────────────────────────────────────────────

if "system_ready" not in st.session_state:
    st.session_state.system_ready = False
if "deepseek_key" not in st.session_state:
    st.session_state.deepseek_key = load_saved_key()
if "messages" not in st.session_state:
    st.session_state.messages = []   # list of {role, content, meta?, query?}
if "rethink_options" not in st.session_state:
    st.session_state.rethink_options = None


def run_query(user_query: str, force_provider: str | None = None, force_model: str | None = None,
              source_filter: str | None = None) -> dict | None:
    payload = {
        "q": user_query,
        "deepseek_api_key": st.session_state.deepseek_key or None,
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

    key_input = st.text_input(
        "API Key", value=st.session_state.deepseek_key, type="password",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Save Key"):
            if key_input.strip():
                save_key(key_input.strip())
                st.session_state.deepseek_key = key_input.strip()
                st.success("Key saved")
            else:
                st.warning("Empty key")
    with c2:
        if st.button("Reset Key"):
            clear_key()
            st.session_state.deepseek_key = ""
            st.success("Key cleared")

    if st.session_state.deepseek_key:
        st.success("Key loaded")
    else:
        st.info("No key set")

    st.divider()

    if st.button("Reset Knowledge Base"):
        try:
            requests.post(f"{API_BASE}/reset", timeout=30)
            st.session_state.messages = []
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


# ── Render a single assistant message: answer + sources + rethink ───────

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


def render_rethink(original_query: str, msg_idx: int) -> None:
    options = fetch_rethink_options()
    if not options:
        return

    labels = [f"{o['provider']}/{o['model']}" for o in options]
    selected = st.selectbox(
        "Rethink with a different model", labels,
        key=f"rethink_select_{msg_idx}", label_visibility="collapsed",
        placeholder="Rethink with a different model...",
    )

    if st.button("🔁 Rethink", key=f"rethink_btn_{msg_idx}"):
        chosen = options[labels.index(selected)]
        with st.spinner(f"Re-answering with {selected}..."):
            data = run_query(original_query, force_provider=chosen["provider"], force_model=chosen["model"])
        if data:
            st.session_state.messages.append({
                "role": "assistant",
                "content": data.get("answer", ""),
                "meta": data,
                "query": original_query,
            })
            st.rerun()


# ── Render conversation history ──────────────────────────────────────────

for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        if msg["role"] == "assistant" and msg.get("meta"):
            meta = msg["meta"]

            if meta.get("needs_clarification"):
                st.info("This question could mean more than one thing in the current documents — try rephrasing with more specifics, or ask about one of the sections above.")

            render_sources(meta.get("sources", []), idx)

            with st.expander("Details"):
                st.json({
                    "role": meta.get("role"),
                    "model": meta.get("model"),
                    "provider": meta.get("provider"),
                    "fallback_used": meta.get("fallback_used"),
                    "grounding_score": meta.get("grounding_score"),
                    "flagged": meta.get("flagged"),
                    "timing": meta.get("timing"),
                })

            if msg.get("query") and not meta.get("needs_clarification"):
                render_rethink(msg["query"], idx)


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
            st.markdown(data.get("answer", ""))

            if data.get("needs_clarification"):
                st.info("This question could mean more than one thing in the current documents — try rephrasing with more specifics, or ask about one of the sections below.")

            new_idx = len(st.session_state.messages)
            render_sources(data.get("sources", []), new_idx)

            with st.expander("Details"):
                st.json({
                    "role": data.get("role"),
                    "model": data.get("model"),
                    "provider": data.get("provider"),
                    "fallback_used": data.get("fallback_used"),
                    "grounding_score": data.get("grounding_score"),
                    "flagged": data.get("flagged"),
                    "timing": data.get("timing"),
                })

            st.session_state.messages.append({
                "role": "assistant",
                "content": data.get("answer", ""),
                "meta": data,
                "query": user_query,
            })