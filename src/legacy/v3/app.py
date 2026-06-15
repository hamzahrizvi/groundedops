import time
import json
import os
import requests
import streamlit as st

API_BASE = "http://localhost:8000"
KEY_FILE = "deepseek_key.json"

st.set_page_config(page_title="GroundedOps", layout="wide")
st.title("GroundedOps")

# ---- Key management helpers ----
def load_saved_key():
    if os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, "r") as f:
                data = json.load(f)
                return data.get("key", "")
        except:
            return ""
    return ""

def save_key(key: str):
    with open(KEY_FILE, "w") as f:
        json.dump({"key": key}, f)

def clear_key():
    if os.path.exists(KEY_FILE):
        os.remove(KEY_FILE)

# ---- Session state ----
if "system_ready" not in st.session_state:
    st.session_state.system_ready = False
if "deepseek_key" not in st.session_state:
    st.session_state.deepseek_key = load_saved_key()

# ---- Sidebar ----
with st.sidebar:
    st.subheader("DeepSeek Settings")
    key_input = st.text_input(
        "API Key",
        value=st.session_state.deepseek_key,
        type="password",
        help="Optional: Provide your DeepSeek API key for fallback / escalation."
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Save Key"):
            if key_input.strip():
                save_key(key_input.strip())
                st.session_state.deepseek_key = key_input.strip()
                st.success("Key saved")
            else:
                st.warning("Empty key – not saved")
    with col2:
        if st.button("Reset Key"):
            clear_key()
            st.session_state.deepseek_key = ""
            st.success("Key cleared")

    st.divider()
    if st.session_state.deepseek_key:
        st.success("✔ DeepSeek key loaded")
    else:
        st.info("No DeepSeek key set – local models only")

    if st.button("Reset Knowledge Base"):
        try:
            requests.post(f"{API_BASE}/reset", timeout=15)
            st.success("Knowledge base reset")
        except Exception as e:
            st.error(f"Reset failed: {e}")

# ---- Wait for backend readiness (with fallback on error) ----
if not st.session_state.system_ready:
    st.subheader("Loading system")
    progress_bar = st.progress(0)
    status_box = st.empty()
    error_box = st.empty()

    deadline = time.time() + 300  # 5 minutes
    while time.time() < deadline:
        try:
            res = requests.get(f"{API_BASE}/status", timeout=5)
            data = res.json()
            progress_bar.progress(int(data.get("progress", 0)))
            status_box.info(data.get("message", "Starting..."))
            if data.get("error"):
                # Do not block – continue with limited functionality
                error_box.warning(f"Startup issue: {data['error']} – continuing with limited functionality")
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

st.success("System ready – models loaded")

# ---- Upload ----
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

# ---- Query ----
st.subheader("Ask")
query_text = st.text_input("Question")
if st.button("Submit") and query_text.strip():
    payload = {
        "q": query_text,
        "deepseek_api_key": st.session_state.deepseek_key or None,
    }
    with st.spinner("Querying..."):
        try:
            res = requests.post(
                f"{API_BASE}/query",
                json=payload,
                timeout=300,
            )
            if not res.ok:
                st.error(res.text)
            else:
                data = res.json()
                st.markdown("### Answer")
                st.write(data.get("answer"))
                st.markdown("### Metadata")
                st.json({
                    "role": data.get("role"),
                    "model": data.get("model"),
                    "provider": data.get("provider"),
                    "fallback_used": data.get("fallback_used"),
                    "grounding_score": data.get("grounding_score"),
                    "flagged": data.get("flagged"),
                    "timing": data.get("timing"),
                    "sources": data.get("sources"),
                })
        except Exception as e:
            st.error(f"API error: {e}")