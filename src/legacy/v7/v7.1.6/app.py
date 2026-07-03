import inspect
import os
import time
import uuid
import html as _html
import requests
import streamlit as st

import keyvault

API_BASE = os.getenv("API_BASE", "http://localhost:8000")

# Detect whether this Streamlit supports st.button(icon=...) (>=1.36) so we
# can use clean Material Symbol line icons, with a unicode-glyph fallback.
_BTN_HAS_ICON = "icon" in inspect.signature(st.button).parameters

st.set_page_config(page_title="GroundedOps", layout="centered", page_icon="✦",
                   initial_sidebar_state="expanded")

# ── Styling: warm "editorial" theme (cream + copper). ───────────────────
# A single, consistent palette — no OS-driven auto dark switch (that left
# Streamlit's own chrome mismatched). To use the dark variant, switch the
# [theme] block in .streamlit/config.toml AND flip the values below to the
# dark set noted in comments. Colours here mirror config.toml so custom
# elements match Streamlit's framework chrome.
st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500;600;700&display=swap');

      /* Default palette (light). The rail toggle re-injects these vars for
         dark mode. Every visible surface below is forced to these vars with
         !important so Streamlit's own theme never leaks through. */
      :root {
        --bg:#F4EDE3; --surface:#FBF6EF; --surface-2:#F0E7DA;
        --ink:#26221D; --muted:#8C8073; --hairline:rgba(176,118,79,0.30);
        --accent:#B0764F; --accent-soft:rgba(176,118,79,0.12);
        --green:#5E7A52; --amber:#B0764F;
      }

      /* ── Global surfaces (forced) ─────────────────────────────────── */
      .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"],
      [data-testid="stHeader"], [data-testid="stToolbar"],
      [data-testid="stBottom"], [data-testid="stBottom"] > div,
      [data-testid="stBottomBlockContainer"] { background:var(--bg) !important; }
      [data-testid="stDecoration"] { display:none; }
      footer { visibility:hidden; }
      .block-container { padding-top:2.2rem; max-width:820px; }

      /* Text colour everywhere */
      .stApp, .stApp p, .stApp li, .stApp label, .stApp span, .stApp div,
      .stMarkdown, [data-testid="stMarkdownContainer"] { color:var(--ink); }

      /* Sidebar */
      [data-testid="stSidebar"] { background:var(--surface) !important;
        border-right:1px solid var(--hairline); }
      [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding:14px 8px; }
      .rail-brand { display:flex; justify-content:center; margin:2px 0 12px 0; }
      .rail-brand img { width:46px; height:46px; }
      .rail-sep { border:none; border-top:1px solid var(--hairline); margin:12px 10px; }

      /* Headings in serif */
      h1, h2, h3, .go-title { font-family:'Playfair Display', Georgia, serif !important;
        color:var(--ink) !important; letter-spacing:-0.01em; font-weight:600; }

      /* Hero */
      .go-hero { display:flex; align-items:center; gap:16px; margin:0 0 4px 0; }
      .go-logo { width:60px; height:60px; flex:0 0 auto; }
      .go-title { font-size:2.6rem; line-height:1; margin:0; }

      /* Main-area buttons: thin outlined copper pills, hover fill */
      [data-testid="stMain"] .stButton > button,
      [data-testid="stMain"] .stFormSubmitButton > button,
      [role="dialog"] .stButton > button, [role="dialog"] .stFormSubmitButton > button {
        background:transparent !important; color:var(--ink) !important;
        border:1px solid var(--hairline) !important; border-radius:999px !important;
        padding:0.5rem 1.1rem; font-weight:500; box-shadow:none !important;
        transition:all 0.18s ease; }
      [data-testid="stMain"] .stButton > button:hover,
      [data-testid="stMain"] .stFormSubmitButton > button:hover,
      [role="dialog"] .stButton > button:hover, [role="dialog"] .stFormSubmitButton > button:hover {
        background:var(--accent-soft) !important; border-color:var(--accent) !important;
        color:var(--accent) !important; }

      /* Inputs, selects, textareas, chat input, uploader — forced surface */
      .stTextInput input, .stNumberInput input, textarea,
      [data-baseweb="input"], [data-baseweb="base-input"],
      [data-baseweb="select"] > div, [data-baseweb="textarea"],
      div[data-testid="stChatInput"], div[data-testid="stChatInput"] > div,
      [data-testid="stChatInputTextArea"],
      [data-testid="stFileUploaderDropzone"] {
        background:var(--surface) !important; color:var(--ink) !important;
        border-color:var(--hairline) !important; border-radius:12px !important; }
      [data-baseweb="input"] input, [data-baseweb="base-input"] input,
      [data-testid="stChatInput"] textarea { background:transparent !important; color:var(--ink) !important; }

      /* Popovers / dropdown menus (selectbox, radio popovers) */
      [data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"],
      [data-testid="stDialog"] > div, [role="dialog"] {
        background:var(--surface) !important; color:var(--ink) !important; }

      /* Bordered containers = answer/dialog cards */
      [data-testid="stVerticalBlockBorderWrapper"] {
        background:var(--surface) !important; border-color:var(--hairline) !important;
        border-radius:14px !important; }

      /* Expanders */
      [data-testid="stExpander"] { border-color:var(--hairline) !important; }
      [data-testid="stExpander"] details { background:var(--surface) !important; }

      /* Chips */
      .chip { display:inline-block; padding:3px 12px; border-radius:999px;
        font-size:0.72rem; font-weight:600; margin:2px 6px 2px 0; line-height:1.7;
        border:1px solid var(--hairline); }
      .chip-grounded { color:var(--green) !important; border-color:var(--green); }
      .chip-unverified { color:var(--amber) !important; border-color:var(--amber); }
      .chip-provider { color:var(--accent) !important; border-color:var(--accent); }
      .chip-clarify { color:var(--accent) !important; border-color:var(--accent); background:var(--accent-soft); }
      .chip-muted { color:var(--muted) !important; }

      /* Status dot */
      .go-dot { height:9px; width:9px; border-radius:50%; display:inline-block;
        margin-right:8px; vertical-align:middle; }
      .go-dot-on { background:var(--accent); box-shadow:0 0 8px var(--accent); }
      .go-dot-off { background:var(--muted); }

      /* Chat bubbles — user right, assistant left */
      .chat-row { display:flex; margin:6px 0; }
      .chat-row.user { justify-content:flex-end; }
      .bubble-user { max-width:78%; padding:10px 15px; border-radius:16px;
        border-bottom-right-radius:4px; background:var(--accent-soft);
        border:1px solid var(--hairline); line-height:1.5; color:var(--ink); }

      /* Sidebar must not be fully dismissible (we have our own toggle) */
      [data-testid="stSidebarCollapseButton"],
      [data-testid="collapsedControl"],
      [data-testid="stSidebarCollapsedControl"] { display:none !important; }
      [data-testid="stChatMessage"] { background:transparent; }

      /* Make button LABELS follow our ink/accent colour (Streamlit renders
         the label in an inner <p>/<span> that ignores the button color). */
      [data-testid="stMain"] .stButton > button p,
      [data-testid="stMain"] .stButton > button span,
      [role="dialog"] .stButton > button p, [role="dialog"] .stButton > button span {
        color:inherit !important; }

      /* Tooltips: readable on our palette, and placed to the side */
      [data-testid="stTooltipContent"], [data-baseweb="tooltip"] div {
        background:var(--ink) !important; color:var(--bg) !important;
        border-radius:8px !important; font-size:0.8rem !important; }

      /* Brand logo. In dark mode a dark-ink logo is rendered WHITE via a
         filter (no ugly light chip) unless a dedicated dark logo is given. */
      .brand-logo-header img { width:240px; max-width:70%; height:auto; }
      .brand-logo-header { margin:0 0 6px 0; display:inline-block; }
      .brand-logo-header.white img, .rail-brand-img.white img { filter:brightness(0) invert(1); }
      .rail-brand-img img { width:40px; height:40px; }

      /* Sidebar must not clip the (larger) icons or our custom tooltips */
      [data-testid="stSidebar"], [data-testid="stSidebarUserContent"] { overflow:visible !important; }
      [data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding:12px 4px !important; }

      /* Custom right-side tooltips for collapsed rail buttons (Streamlit's
         own tooltip can't be forced to the right, so we roll our own).
         The per-key content is injected in the collapsed CSS below. */
      [data-testid="stSidebar"] [class*="st-key-rail_"] { position:relative; }
      [data-testid="stSidebar"] [class*="st-key-rail_"]:hover::after {
        position:absolute; left:calc(100% + 4px); top:50%;
        transform:translateY(-50%); background:var(--ink); color:var(--bg);
        padding:4px 10px; border-radius:8px; white-space:nowrap; font-size:0.78rem;
        z-index:1000; pointer-events:none; box-shadow:0 2px 8px rgba(0,0,0,0.25); }

      /* ── Animations: page-load ring + "thinking" dots ─────────────── */
      @keyframes go-spin { to { transform:rotate(360deg); } }
      @keyframes go-pulse { 0%,100%{opacity:0.35;} 50%{opacity:1;} }
      @keyframes go-bounce { 0%,80%,100%{transform:translateY(0);opacity:0.4;}
        40%{transform:translateY(-6px);opacity:1;} }
      .go-loader { display:flex; flex-direction:column; align-items:center;
        gap:14px; padding:40px 0; }
      .go-ring { width:46px; height:46px; border-radius:50%;
        border:3px solid var(--hairline); border-top-color:var(--accent);
        animation:go-spin 0.9s linear infinite; }
      .go-load-label { color:var(--muted); font-style:italic; letter-spacing:0.02em; }
      .go-thinking { display:flex; align-items:center; gap:6px; padding:6px 2px; }
      .go-thinking .dot { width:8px; height:8px; border-radius:50%;
        background:var(--accent); animation:go-bounce 1.2s infinite; }
      .go-thinking .dot:nth-child(2){ animation-delay:0.15s; }
      .go-thinking .dot:nth-child(3){ animation-delay:0.30s; }
      .go-thinking .label { color:var(--muted); font-style:italic; margin-left:4px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Generic minimal mark in the editorial style (thin copper arcs + a spark).
# Deliberately NOT a seed/leaf mark — this is our own decorative glyph.
_LOGO_SVG = """
<svg class="go-logo" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
  <circle cx="24" cy="24" r="18" stroke="var(--accent)" stroke-width="1.4" opacity="0.55"/>
  <circle cx="24" cy="24" r="11" stroke="var(--accent)" stroke-width="1.4"/>
  <circle cx="24" cy="24" r="3" fill="var(--accent)"/>
  <path d="M39 12 l1.4 3.2 l3.2 1.4 l-3.2 1.4 l-1.4 3.2 l-1.4 -3.2 l-3.2 -1.4 l3.2 -1.4 z"
        fill="var(--accent)"/>
</svg>
"""

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
import base64 as _b64

# Read UI state up front so the logo/rail can react to it.
rail_expanded = st.session_state.get("rail_expanded", False)
dark_mode = st.session_state.get("dark_mode", False)


def _data_uri(relpath: str) -> str | None:
    fp = os.path.join(_BASE_DIR, relpath)
    if not os.path.exists(fp):
        return None
    mime = "image/svg+xml" if fp.endswith(".svg") else "image/png"
    try:
        with open(fp, "rb") as f:
            return f"data:{mime};base64,{_b64.b64encode(f.read()).decode('ascii')}"
    except Exception:
        return None


def _icon_data_uri(name: str) -> str | None:
    for ext in ("png", "svg"):
        uri = _data_uri(os.path.join("assets", "icons", f"{name}.{ext}"))
        if uri:
            return uri
    return None


# Logo: prefer a dedicated dark logo in dark mode; else fall back to the
# main logo and render it WHITE in dark mode (via CSS filter) so it stays
# visible on the dark background — no light chip.
_HAS_LOGO_DARK = _data_uri("assets/logo_dark.png") or _data_uri("assets/logo_dark.svg")
if dark_mode and _HAS_LOGO_DARK:
    _LOGO_DATA = _HAS_LOGO_DARK
    _LOGO_WHITE = False
else:
    _LOGO_DATA = (_data_uri("assets/logo.png") or _data_uri("assets/logo.svg")
                  or _data_uri("logo.png") or _data_uri("logo.svg"))
    _LOGO_WHITE = bool(_LOGO_DATA) and dark_mode

# Custom rail icons (collapsed mode only — see rail section).
_RAIL_ICON_NAMES = {
    "rail_settings": "key", "rail_docs": "folder", "rail_new": "new",
    "rail_reset": "reset", "rail_theme_dark": "theme_dark",
    "rail_theme_light": "theme_light",
}
_custom_icons = {k: _icon_data_uri(v) for k, v in _RAIL_ICON_NAMES.items()}

# Right-side tooltip labels per rail button key (collapsed mode).
_RAIL_TOOLTIPS = {
    "rail_toggle": "Expand menu",
    "rail_settings": "DeepSeek key & settings",
    "rail_docs": "Documents",
    "rail_new": "New chat",
    "rail_reset": "Reset knowledge base",
    "rail_theme_dark": "Dark mode",
    "rail_theme_light": "Light mode",
}


def _collapsed_icon_css() -> str:
    """CSS for collapsed rail: paint custom PNG/SVG icons (2x, centered, no
    text) AND emit the per-key right-side tooltip labels."""
    parts = []
    for key, uri in _custom_icons.items():
        if uri:
            parts.append(
                f".st-key-{key} button {{ background-image:url('{uri}') !important;"
                f" background-repeat:no-repeat; background-position:center;"
                f" background-size:34px 34px !important; }}"
                f".st-key-{key} button p, .st-key-{key} button [data-testid='stIconMaterial'] {{"
                f" opacity:0 !important; }}"
            )
    # Per-key tooltip content.
    for key, label in _RAIL_TOOLTIPS.items():
        safe = label.replace('"', '\\"')
        parts.append(f'.st-key-{key}:hover::after {{ content:"{safe}"; }}')
    return "".join(parts)


# ── Header logo (top-left) ───────────────────────────────────────────────
if _LOGO_DATA:
    _cls = " white" if _LOGO_WHITE else ""
    st.markdown(
        f'<div class="brand-logo-header{_cls}"><img src="{_LOGO_DATA}"/></div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        f'<div class="go-hero">{_LOGO_SVG}<span class="go-title">GroundedOps</span></div>',
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


# ── Panels (Settings / Documents) shown as dialogs from the icon rail ───

def _get_dialog():
    # st.dialog landed as st.dialog (>=1.37) / experimental_dialog earlier.
    return getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)


def _fetch_stats() -> dict:
    try:
        return requests.get(f"{API_BASE}/stats", timeout=10).json()
    except Exception:
        return {}


def settings_body():
    key_is_set = keyvault.has_key()
    dot = "go-dot-on" if key_is_set else "go-dot-off"
    label = "Key saved · encrypted, hidden" if key_is_set else "No key set"
    st.markdown(f'<span class="go-dot {dot}"></span>{label}', unsafe_allow_html=True)

    # Form with clear_on_submit=True: the field is wiped the instant it's
    # submitted, so a typed key is never left on screen (and never re-shown).
    with st.form("deepseek_key_form", clear_on_submit=True):
        key_input = st.text_input(
            "DeepSeek API key" + (" (replaces existing)" if key_is_set else ""),
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


def documents_body():
    stats = _fetch_stats()
    sources = stats.get("sources", [])
    st.caption(f"{len(sources)} document(s) · {stats.get('total_chunks', 0)} chunks")

    up = st.file_uploader("Add .txt / .pdf / .docx", accept_multiple_files=True,
                          type=["txt", "pdf", "docx"])
    if up:
        # Upload ALL selected files in a single pass. The old code called
        # st.rerun() after the first successful file, which aborted the loop
        # before the rest were sent — hence "only one at a time". We track
        # already-uploaded (name,size) signatures so the rerun at the end
        # doesn't re-upload the files the uploader still holds in state.
        done = st.session_state.setdefault("uploaded_sig", set())
        new_count = 0
        for f in up:
            sig = f"{f.name}:{getattr(f, 'size', len(f.getvalue()))}"
            if sig in done:
                continue
            with st.spinner(f"Uploading {f.name}..."):
                try:
                    res = requests.post(
                        f"{API_BASE}/upload",
                        files={"file": (f.name, f.getvalue(), f.type or "application/octet-stream")},
                        timeout=300,
                    )
                    if res.ok:
                        done.add(sig)
                        new_count += 1
                    else:
                        st.error(res.text)
                except Exception as e:
                    st.error(f"Upload failed for {f.name}: {e}")
        if new_count:
            st.success(f"Added {new_count} document(s)")
            st.rerun()

    if sources:
        st.markdown("**Loaded documents**")
        with st.container(height=240):
            for src in sources:
                name_col, x_col = st.columns([6, 1])
                name_col.write(src)
                if x_col.button("✕", key=f"del_{src}", help=f"Remove {src}"):
                    res = requests.post(f"{API_BASE}/delete_source", json={"source": src}, timeout=30)
                    if res.ok:
                        st.session_state.messages = []
                        st.rerun()
                    else:
                        st.error(res.text)


_dialog = _get_dialog()

if _dialog:
    @_dialog("DeepSeek settings")
    def open_settings():
        settings_body()

    @_dialog("Documents")
    def open_documents():
        documents_body()
else:
    # Fallback for older Streamlit without dialogs: toggle an inline panel.
    def open_settings():
        st.session_state.active_panel = "settings"
        st.rerun()

    def open_documents():
        st.session_state.active_panel = "documents"
        st.rerun()


def do_new_conversation():
    try:
        requests.post(f"{API_BASE}/clear_session",
                     json={"session_id": st.session_state.session_id}, timeout=15)
    except Exception:
        pass
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []
    st.rerun()


def do_reset_kb():
    try:
        requests.post(f"{API_BASE}/reset", timeout=30)
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()
    except Exception as e:
        st.error(f"Reset failed: {e}")


def _reset_confirm_body():
    st.warning(
        "This permanently deletes **all** uploaded documents and their indexed "
        "chunks from the knowledge base. This cannot be undone."
    )
    cc = st.columns(2)
    if cc[0].button("Cancel", use_container_width=True, key="reset_cancel"):
        st.session_state.pop("confirm_reset", None)
        st.rerun()
    if cc[1].button("Yes, reset everything", use_container_width=True, key="reset_go"):
        st.session_state.pop("confirm_reset", None)
        do_reset_kb()


if _dialog:
    @_dialog("Reset knowledge base?")
    def confirm_reset():
        _reset_confirm_body()
else:
    def confirm_reset():
        st.session_state.confirm_reset = True
        st.rerun()


def rail_btn(label_text: str, material: str, glyph: str, key: str, help_text: str,
             expanded: bool) -> bool:
    """Icon rail button. Uses a clean Material Symbol icon when supported,
    else falls back to a unicode glyph. Shows a text label only when the
    rail is expanded (Claude-style)."""
    label = label_text if expanded else "\u200b"  # zero-width when collapsed
    if _BTN_HAS_ICON:
        return st.button(label, icon=material, key=key, help=help_text,
                         use_container_width=expanded)
    prefix = (glyph + "  ") if expanded else glyph
    return st.button(f"{prefix}{label_text if expanded else ''}".strip() or glyph,
                     key=key, help=help_text, use_container_width=expanded)


# ── Left icon rail (expandable / retractable) ────────────────────────────
# (rail_expanded / dark_mode were computed near the top so the logo could
# react to them.)

# Runtime palette swap. Our colours live in CSS variables, so a dark/light
# toggle is just a matter of re-declaring :root here (this injection comes
# after the base <style>, so it wins).
if dark_mode:
    _palette = """
      :root {
        --bg:#1B1714; --surface:#241E19; --surface-2:#2C251F;
        --ink:#EDE4D7; --muted:#A29688; --hairline:rgba(201,138,94,0.34);
        --accent:#C98A5E; --accent-soft:rgba(201,138,94,0.18);
        --green:#9CB58C; --amber:#C98A5E; }
    """
else:
    _palette = """
      :root {
        --bg:#F4EDE3; --surface:#FBF6EF; --surface-2:#F0E7DA;
        --ink:#26221D; --muted:#8C8073; --hairline:rgba(176,118,79,0.30);
        --accent:#B0764F; --accent-soft:rgba(176,118,79,0.12);
        --green:#5E7A52; --amber:#B0764F; }
    """

# State-dependent sidebar width + button shape. Collapsed = big icon-only
# buttons (2x, transparent, no box); expanded = full-width labelled rows.
_side_w = 236 if rail_expanded else 96
if rail_expanded:
    _btn_shape = ("width:100%; justify-content:flex-start; gap:10px; border-radius:12px;"
                  "padding:0.55rem 0.9rem; margin:5px 0; font-size:0.98rem;")
else:
    _btn_shape = ("width:64px; height:64px; border-radius:18px; padding:0; margin:10px auto;"
                  "display:flex; align-items:center; justify-content:center; font-size:1.7rem;")

# Force sidebar buttons fully transparent (no white box), both states.
_btn_css = f"""
      [data-testid="stSidebar"] .stButton > button,
      [data-testid="stSidebar"] button {{
        background:transparent !important; border:none !important;
        box-shadow:none !important; color:var(--ink) !important; }}
      [data-testid="stSidebar"] .stButton > button {{ {_btn_shape} }}
      [data-testid="stSidebar"] .stButton > button:hover {{
        background:var(--accent-soft) !important; color:var(--accent) !important; }}
"""
# Paint custom PNG/SVG icons only in collapsed mode (icon-only buttons).
_icon_css = "" if rail_expanded else _collapsed_icon_css()

st.markdown(
    f"<style>{_palette}[data-testid='stSidebar']{{width:{_side_w}px !important;"
    f"min-width:{_side_w}px !important;}}{_btn_css}{_icon_css}</style>",
    unsafe_allow_html=True,
)

with st.sidebar:
    if rail_expanded:
        # Logo + collapse toggle beside it, at the top.
        bc = st.columns([3, 1], vertical_alignment="center")
        with bc[0]:
            if _LOGO_DATA:
                _c = " white" if _LOGO_WHITE else ""
                st.markdown(f'<div class="rail-brand-img{_c}"><img src="{_LOGO_DATA}"/></div>',
                            unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="rail-brand">{_LOGO_SVG}</div>', unsafe_allow_html=True)
        with bc[1]:
            if rail_btn("", ":material/left_panel_close:", "«", "rail_toggle",
                        "Collapse menu", True):
                st.session_state.rail_expanded = False
                st.rerun()
    else:
        # Collapsed: just the expand toggle at top (no logo). This is the
        # only control needed to open the menu.
        if rail_btn("", ":material/left_panel_open:", "»", "rail_toggle",
                    "Expand menu", False):
            st.session_state.rail_expanded = True
            st.rerun()

    st.markdown('<hr class="rail-sep"/>', unsafe_allow_html=True)

    if rail_btn("DeepSeek key", ":material/key:", "🔑", "rail_settings",
                "DeepSeek key & settings", rail_expanded):
        open_settings()
    if rail_btn("Documents", ":material/folder:", "🗂", "rail_docs",
                "Documents — upload & manage", rail_expanded):
        open_documents()

    st.markdown('<hr class="rail-sep"/>', unsafe_allow_html=True)

    if rail_btn("New chat", ":material/add:", "＋", "rail_new",
                "New conversation", rail_expanded):
        do_new_conversation()
    if rail_btn("Reset KB", ":material/restart_alt:", "↻", "rail_reset",
                "Reset knowledge base", rail_expanded):
        confirm_reset()

    # Light / dark toggle
    _theme_mat = ":material/light_mode:" if dark_mode else ":material/dark_mode:"
    _theme_label = "Light mode" if dark_mode else "Dark mode"
    _theme_key = "rail_theme_light" if dark_mode else "rail_theme_dark"
    if rail_btn(_theme_label, _theme_mat, "☀" if dark_mode else "☾",
                _theme_key, "Toggle light / dark", rail_expanded):
        st.session_state.dark_mode = not dark_mode
        st.rerun()

# Inline fallback panels (only when st.dialog isn't available)
if not _dialog and st.session_state.get("active_panel"):
    panel = st.session_state.active_panel
    with st.container(border=True):
        top = st.columns([6, 1])
        top[0].subheader("Settings" if panel == "settings" else "Documents")
        if top[1].button("Close", key="panel_close"):
            st.session_state.active_panel = None
            st.rerun()
        (settings_body if panel == "settings" else documents_body)()

if not _dialog and st.session_state.get("confirm_reset"):
    with st.container(border=True):
        st.subheader("Reset knowledge base?")
        _reset_confirm_body()


# ── Wait for backend readiness ──────────────────────────────────────────

if not st.session_state.system_ready:
    loader = st.empty()
    status_box = st.empty()
    error_box = st.empty()

    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            res = requests.get(f"{API_BASE}/status", timeout=5)
            data = res.json()
            _msg = data.get("message", "Starting…")
            loader.markdown(
                f'<div class="go-loader"><div class="go-ring"></div>'
                f'<div class="go-load-label">{_html.escape(_msg)}</div></div>',
                unsafe_allow_html=True,
            )
            if data.get("error"):
                error_box.warning(f"Startup issue: {data['error']} — continuing with limited functionality")
                st.session_state.system_ready = True
                st.rerun()
            if data.get("ready"):
                st.session_state.system_ready = True
                st.rerun()
        except Exception:
            loader.markdown(
                '<div class="go-loader"><div class="go-ring"></div>'
                '<div class="go-load-label">Waiting for backend…</div></div>',
                unsafe_allow_html=True,
            )
        time.sleep(1)

    st.error("System did not become ready in time.")
    st.stop()


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

    OTHER = "Other (let me type it)"
    SKIP = "Skip"
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
            if st.button(label, key=f"src_{msg_idx}_{i}"):
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

    with st.expander("Rethink with another model"):
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
        st.caption(f"Searched for: _{meta['resolved_query']}_")

    render_sources(meta.get("sources", []), msg_idx)

    if query and meta.get("role") not in ("clarify", "rejected"):
        render_rethink(query, msg_idx, meta)

    render_meta_expander(meta)


# ── Conversation ─────────────────────────────────────────────────────────

def render_user_turn(text: str) -> None:
    """User message: right-aligned bubble."""
    st.markdown(
        f'<div class="chat-row user"><div class="bubble-user">{_html.escape(text)}</div></div>',
        unsafe_allow_html=True,
    )


def render_assistant_turn(meta: dict, content: str, msg_idx: int, query: str | None) -> None:
    """Assistant message: left-aligned, in a bordered card ~80% width so
    interactive widgets (sources, rethink, dropdowns) keep working."""
    left, _ = st.columns([5, 1])
    with left:
        with st.container(border=True):
            render_assistant_block(meta, content, msg_idx, query)


for idx, msg in enumerate(st.session_state.messages):
    if msg["role"] == "assistant" and msg.get("meta"):
        render_assistant_turn(msg["meta"], msg["content"], idx, msg.get("query"))
    else:
        render_user_turn(msg["content"])


# ── Chat input (handles typing, "ask more", and clarify picks) ──────────

pending = st.session_state.pop("pending_query", None) if "pending_query" in st.session_state else None
pending_filter = st.session_state.pop("pending_source_filter", None) if "pending_source_filter" in st.session_state else None

user_query = st.chat_input("Ask a question about your documents") or pending

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})
    render_user_turn(user_query)

    thinking = st.empty()
    thinking.markdown(
        '<div class="go-thinking"><span class="dot"></span><span class="dot"></span>'
        '<span class="dot"></span><span class="label">Thinking…</span></div>',
        unsafe_allow_html=True,
    )
    try:
        data = run_query(user_query, source_filter=pending_filter)
    except Exception as e:
        st.error(f"API error: {e}")
        data = None
    thinking.empty()

    if data:
        new_idx = len(st.session_state.messages)
        render_assistant_turn(data, data.get("answer", ""), new_idx, user_query)
        st.session_state.messages.append({
            "role": "assistant", "content": data.get("answer", ""),
            "meta": data, "query": user_query,
        })
