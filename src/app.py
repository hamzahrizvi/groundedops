import json
import os
import time
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://localhost:8000")
KEY_FILE = ".deepseek_key.json"

st.set_page_config(page_title="GroundedOps", layout="wide")
st.title("GroundedOps")


def load_saved_key() -> str:
    if os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("key", "")
        except Exception:
            return ""
    return ""


def save_key(key: str) -> None:
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        json.dump({"key": key}, f)


def clear_key() -> None:
    if os.path.exists(KEY_FILE):
        os.remove(KEY_FILE)


if "system_ready" not in st.session_state:
    st.session_state.system_ready = False
if "deepseek_key" not in st.session_state:
    st.session_state.deepseek_key = load_saved_key()
if "messages" not in st.session_state:
    st.session_state.messages = []


with st.sidebar:
    st.subheader("DeepSeek Settings")

    key_input = st.text_input(
        "API Key",
        value=st.session_state.deepseek_key,
        type="password",
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
                if row2.button("X", key=f"del_{src}"):
                    res = requests.post(
                        f"{API_BASE}/delete_source",
                        json={"source": src},
                        timeout=30,
                    )
                    if res.ok:
                        st.session_state.messages = []
                        st.success(f"Removed: {src}")
                        st.rerun()
                    else:
                        st.error(res.text)

    except Exception as e:
        st.warning(f"Could not load documents: {e}")


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
                error_box.warning(
                    f"Startup issue: {data['error']} — continuing with limited functionality"
                )
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
                if res.ok:
                    st.write(res.json())
                else:
                    st.error(res.text)
            except Exception as e:
                st.error(f"Upload failed for {f.name}: {e}")

st.divider()
st.subheader("Conversation")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("meta"):
            with st.expander("Details"):
                st.json(msg["meta"])

user_query = st.chat_input("Ask a question")

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})

    with st.chat_message("user"):
        st.markdown(user_query)

    payload = {
        "q": user_query,
        "deepseek_api_key": st.session_state.deepseek_key or None,
    }

    with st.chat_message("assistant"):
        with st.spinner("Querying..."):
            try:
                res = requests.post(f"{API_BASE}/query", json=payload, timeout=300)

                if not res.ok:
                    st.error(res.text)
                else:
                    data = res.json()
                    answer = data.get("answer", "")
                    st.markdown(answer)

                    meta = {
                        "role": data.get("role"),
                        "model": data.get("model"),
                        "provider": data.get("provider"),
                        "fallback_used": data.get("fallback_used"),
                        "grounding_score": data.get("grounding_score"),
                        "flagged": data.get("flagged"),
                        "timing": data.get("timing"),
                        "sources": data.get("sources"),
                    }

                    with st.expander("Details"):
                        st.json(meta)

                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": answer,
                        "meta": meta,
                    })

            except Exception as e:
                st.error(f"API error: {e}")