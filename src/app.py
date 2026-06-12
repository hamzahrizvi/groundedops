import streamlit as st
import requests
from datetime import datetime

API = "http://localhost:8000"

st.set_page_config(
    page_title="RAG Assistant",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Tighten default padding */
.block-container { padding-top: 1.5rem; }

/* Inline badge spans */
.badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 10px;
    font-size: 0.72rem;
    font-weight: 600;
    margin: 2px 3px 2px 0;
    letter-spacing: 0.2px;
}
.b-green  { background:#052e0f; color:#4ade80; border:1px solid #166534; }
.b-yellow { background:#2d1e00; color:#fbbf24; border:1px solid #b45309; }
.b-red    { background:#2d0000; color:#f87171; border:1px solid #b91c1c; }
.b-blue   { background:#001c3d; color:#60a5fa; border:1px solid #1d4ed8; }
.b-purple { background:#1a0030; color:#c084fc; border:1px solid #7e22ce; }
.b-gray   { background:#1c1c1c; color:#9ca3af; border:1px solid #374151; }
.b-orange { background:#2d1000; color:#fb923c; border:1px solid #c2410c; }

/* Source chips */
.src-chip {
    display: inline-block;
    padding: 2px 8px;
    background:#1e293b;
    border-radius:5px;
    font-size:0.73rem;
    color:#94a3b8;
    border:1px solid #334155;
    margin:2px 3px 2px 0;
}

/* Query input stretch */
div[data-testid="stTextInput"] input {
    font-size: 1rem;
}

/* Sidebar file entries */
.doc-row {
    padding: 5px 8px;
    border-left: 3px solid #3b82f6;
    background: #111827;
    border-radius: 0 5px 5px 0;
    margin: 4px 0;
    font-size: 0.82rem;
    color: #cbd5e1;
}
.doc-row span { color: #64748b; font-size: 0.72rem; }

/* Subtle meta row */
.meta-row { margin-top: 0.6rem; padding-top: 0.6rem; border-top: 1px solid rgba(255,255,255,0.07); }
</style>
""", unsafe_allow_html=True)


# ── Session state ──────────────────────────────────────────────────────────────
if "history"     not in st.session_state: st.session_state.history     = []
if "upload_log"  not in st.session_state: st.session_state.upload_log  = {}  # filename→chunks
if "query_count" not in st.session_state: st.session_state.query_count = 0
if "flag_count"  not in st.session_state: st.session_state.flag_count  = 0


# ── Helpers ────────────────────────────────────────────────────────────────────

def api_get(path: str) -> dict | None:
    try:
        r = requests.get(f"{API}{path}", timeout=5)
        return r.json() if r.ok else None
    except Exception:
        return None


def api_post(path: str, **kwargs) -> dict | None:
    try:
        r = requests.post(f"{API}{path}", timeout=90, **kwargs)
        return r.json() if r.ok else None
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def badge(text: str, cls: str) -> str:
    return f'<span class="badge {cls}">{text}</span>'


def grounding_html(score: float | None, flagged: bool) -> str:
    if score is None:
        return ""
    if flagged:
        return badge(f"✗ Flagged {score:.2f}", "b-red")
    elif score >= 0.7:
        return badge(f"✓ {score:.2f}", "b-green")
    elif score >= 0.45:
        return badge(f"~ {score:.2f}", "b-yellow")
    else:
        return badge(f"↓ {score:.2f}", "b-orange")


def render_result(item: dict, compact: bool = False):
    """Render a result dict (from history) as a card."""
    data      = item["data"]
    query     = item["query"]
    ts        = item.get("ts", "")

    answer    = data.get("answer", "")
    role      = data.get("role", "")
    model     = data.get("model", "none")
    provider  = data.get("provider", "")
    fallback  = data.get("fallback_used", False)
    grounding = data.get("grounding_score")
    flagged   = data.get("flagged", False)
    sources   = data.get("sources") or []
    timing    = data.get("timing", {})
    reason    = data.get("reason", "")
    rejected  = role == "rejected"

    if compact:
        label = f"🕐 {ts}  —  {query[:75]}{'…' if len(query)>75 else ''}"
        with st.expander(label, expanded=False):
            _render_card_body(answer, role, model, provider, fallback,
                              grounding, flagged, sources, timing, reason, rejected)
        return

    _render_card_body(answer, role, model, provider, fallback,
                      grounding, flagged, sources, timing, reason, rejected)


def _render_card_body(answer, role, model, provider, fallback,
                       grounding, flagged, sources, timing, reason, rejected):

    with st.container(border=True):

        if rejected:
            st.warning(f"**No relevant content found.** {reason or ''}", icon="🔍")

        else:
            st.markdown(answer)

            # ── Metadata badges ──────────────────────────────
            parts = []
            if role    and role  != "none":  parts.append(badge(role,     "b-blue"))
            if model   and model != "none":  parts.append(badge(model,    "b-purple"))
            if provider and provider!="none":parts.append(badge(provider, "b-gray"))
            if fallback:                     parts.append(badge("⚡ fallback", "b-orange"))
            parts.append(grounding_html(grounding, flagged))

            st.markdown(
                f'<div class="meta-row">{"".join(parts)}</div>',
                unsafe_allow_html=True,
            )

            # ── Sources ──────────────────────────────────────
            if sources:
                chips = "".join(
                    f'<span class="src-chip">📄 {s}</span>'
                    for s in sources if s
                )
                st.markdown(chips, unsafe_allow_html=True)

    # ── Timing (outside container so expander renders cleanly) ───
    if timing and not rejected:
        with st.expander("⏱ Timing breakdown", expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total",      f"{timing.get('total_time',      0):.2f}s")
            c2.metric("LLM",        f"{timing.get('llm_time',        0):.2f}s")
            c3.metric("Retrieval",  f"{timing.get('retrieval_time',  0):.2f}s")
            c4.metric("Extraction", f"{timing.get('extraction_time', 0):.2f}s")


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🔍 RAG Assistant")

    # Status pill
    status = api_get("/status")
    if status:
        st.success("API online", icon="🟢")
    else:
        st.error("API offline", icon="🔴")
        st.caption("Start with: `uvicorn main:app --reload`")

    st.divider()

    # ── Documents section ──────────────────────────────────────────
    st.subheader("📁 Knowledge Base")

    if status:
        kb_docs   = status.get("doc_count",    0)
        kb_chunks = status.get("total_chunks", 0)
        st.caption(f"{kb_docs} document{'s' if kb_docs!=1 else ''}  •  {kb_chunks} chunks")

        # Show stored sources
        for src in status.get("sources", []):
            st.markdown(
                f'<div class="doc-row">📄 {src}</div>',
                unsafe_allow_html=True,
            )

    # Upload widget
    st.markdown("**Upload documents**")
    uploaded = st.file_uploader(
        "PDF, DOCX or TXT",
        type=["pdf", "docx", "txt"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if uploaded:
        for f in uploaded:
            if f.name not in st.session_state.upload_log:
                with st.spinner(f"Ingesting {f.name}…"):
                    result = api_post("/upload", files={"file": (f.name, f, f.type)})
                if result:
                    chunks = result.get("chunks_added", 0)
                    warn   = result.get("warning", "")
                    if chunks:
                        st.session_state.upload_log[f.name] = chunks
                        st.success(f"✓ {f.name} — {chunks} chunks")
                    else:
                        st.warning(f"⚠ {f.name}: {warn or 'No chunks added'}")
                else:
                    st.error(f"Failed to upload {f.name}")

    st.divider()

    # ── Session stats ──────────────────────────────────────────────
    st.subheader("📊 This Session")
    m1, m2, m3 = st.columns(3)
    m1.metric("Queries",  st.session_state.query_count)
    m2.metric("Answered", st.session_state.query_count - st.session_state.flag_count)
    m3.metric("Flagged",  st.session_state.flag_count)

    st.divider()

    # ── Controls ───────────────────────────────────────────────────
    st.subheader("⚙️ Controls")

    if st.button("🗑️ Reset Knowledge Base", use_container_width=True):
        with st.spinner("Resetting…"):
            api_post("/reset")
        st.session_state.upload_log  = {}
        st.session_state.history     = []
        st.session_state.query_count = 0
        st.session_state.flag_count  = 0
        st.rerun()

    if st.button("🧹 Clear History", use_container_width=True):
        st.session_state.history     = []
        st.session_state.query_count = 0
        st.session_state.flag_count  = 0
        st.rerun()


# ── Main area ──────────────────────────────────────────────────────────────────
st.header("Ask your documents")

with st.form("query_form", clear_on_submit=True):
    col_input, col_btn = st.columns([5, 1])
    with col_input:
        q = st.text_input(
            "Question",
            placeholder="e.g. give me the checklist before leaving site",
            label_visibility="collapsed",
        )
    with col_btn:
        submitted = st.form_submit_button("Ask →", use_container_width=True)

# ── Handle query ───────────────────────────────────────────────────────────────
if submitted and q and q.strip():
    with st.spinner("Searching and generating…"):
        data = api_post("/query", params={"q": q.strip()})

    if data:
        ts = datetime.now().strftime("%H:%M:%S")
        st.session_state.history.insert(0, {"query": q.strip(), "data": data, "ts": ts})
        st.session_state.query_count += 1
        if data.get("flagged"):
            st.session_state.flag_count += 1

elif submitted and not q.strip():
    st.warning("Please enter a question.", icon="💬")

# ── Latest result ──────────────────────────────────────────────────────────────
if st.session_state.history:
    latest = st.session_state.history[0]
    st.markdown(f"**Q:** {latest['query']}")
    render_result(latest, compact=False)

# ── History ────────────────────────────────────────────────────────────────────
if len(st.session_state.history) > 1:
    st.divider()
    st.subheader("📜 Query History")
    for item in st.session_state.history[1:]:
        render_result(item, compact=True)