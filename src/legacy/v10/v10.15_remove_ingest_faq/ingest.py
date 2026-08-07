import os
import re
import logging
import tempfile

from parsing import extract_text
from chunking import chunk_text
from embeddings import embed_texts
from db import get_collection

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# BREADCRUMB ENRICHMENT  (Change #2 — fixes the credential-disambiguation bug)
#
# Problem it solves: the MyConnect app login block ("Step 3: Login to the
# Hub … user1/password1") and the device-registration block ("Registering a
# New Device … apiuser/apipassword") are separate, cleanly-chunked units, but
# to the embedder/reranker they look near-identical ("default username/
# password"). The API block even contains the word "app", so for the query
# "default login credentials for the MyConnect app" the retriever ranks the
# WRONG block on top and the login chunk never reaches the context.
#
# Fix: prepend each chunk with a breadcrumb — the document name plus the
# nearest recognized section/step header. "[MyConnect_Environment — Step 3:
# Login to the Hub]" vs "[MyConnect_Environment — Registering a New Device]"
# gives the embedder, BM25, and the cross-encoder the section-identity signal
# they currently lack. The prefix is stored, so it participates in retrieval;
# main.py strips it before the text is used for generation/grounding.
#
# LIMITATION: SECTION_TITLES below is tuned to THIS document set. For a
# generic corpus, replace this with layout/font-based header detection
# (see the pdfplumber "layout-aware ingestion" suggestion). Extend the list
# when adding docs whose section titles aren't caught by the generic
# title-case fallback in _detect_header().
# ──────────────────────────────────────────────────────────────────────────

_STEP_RE = re.compile(r"^step\s+\d+", re.IGNORECASE)

# Known section headings across the four ITL docs, worth surfacing verbatim.
SECTION_TITLES = [
    "registering a new device",
    "screen editing",
    "viewing accounts",
    "account roles",
    "system architecture",
    "communication flow",
    "http ports",
    "login to the hub",
    "access settings to add devices",
    "adding devices",
    "general description",
    "key features",
    "mechanical installation",
    "software installation",
    "product introduction",
    "support and troubleshooting",
    "general troubleshooting",
    "face recognition",
    "technical data",
]
_SECTION_RE = re.compile(r"^(?:" + "|".join(re.escape(t) for t in SECTION_TITLES) + r")",
                         re.IGNORECASE)


def _detect_header(line: str) -> str | None:
    """Return the line if it looks like a section/step header, else None."""
    l = line.strip()
    if not l:
        return None
    if _SECTION_RE.match(l):
        return l
    if _STEP_RE.match(l):
        return l
    return None


def _breadcrumb(chunk: str) -> str | None:
    """
    Find the nearest section/step header inside a chunk. Takes the FIRST
    recognized header (the one the chunk's content sits under), and glues a
    short wrapped continuation line onto a Step header (PDF extraction often
    splits "Step 3: Login to" / "the Hub" across two lines).
    """
    lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        h = _detect_header(ln)
        if not h:
            continue
        if (h.lower().startswith("step")
                and not h.endswith((".", ":"))
                and i + 1 < len(lines)
                and len(lines[i + 1].split()) <= 4
                and not _detect_header(lines[i + 1])):
            h = h + " " + lines[i + 1]
        return h
    return None


def _enrich_chunks(chunks: list[str], filename: str) -> list[str]:
    """
    Prepend "[<doc> — <section>]" (or "[<doc>]" when no header is found) to
    each chunk so section identity travels into embedding, BM25, and rerank.
    Stripped again in main.py before generation/grounding.
    """
    doc = os.path.splitext(filename)[0]
    out = []
    for c in chunks:
        crumb = _breadcrumb(c)
        prefix = f"[{doc} — {crumb}]" if crumb else f"[{doc}]"
        out.append(f"{prefix}\n{c}")
    return out


def _generate_questions(chunk: str, n: int = 4, api_keys: dict | None = None) -> list[str]:
    """DOC2QUERY (v8.5): generate likely user questions per chunk at ingest.

    Provider is controlled by INGEST_PROVIDER (v10.x):
      "deepseek" / "openai" / "anthropic"  -> that API (fast; recommended
                                              for large docs — turns an
                                              hour of local CPU into minutes)
      "local"                              -> local mistral (slow on CPU)
      "auto" (default)                     -> API if a key is available,
                                              else local mistral
    DOC2QUERY=off disables entirely (ingestion behaves as v8.4.x)."""
    if os.getenv("DOC2QUERY", "on").strip().lower() in ("off", "0", "false"):
        return []
    prompt = (
        "You write search queries. Given the documentation excerpt below, "
        f"write {n} short questions a support user might ask that this "
        "excerpt answers. One per line, no numbering, no preamble, "
        "questions only.\n\n---\n" + chunk[:1500]
    )
    keys = api_keys or {}
    provider = os.getenv("INGEST_PROVIDER", "auto").strip().lower()
    # v10.4: fall back to ENV keys when none were passed in (headless async
    # ingest passes {}). Without this, "auto" never found a key and
    # silently used local mistral even when DEEPSEEK_API_KEY was set.
    keys = {
        "deepseek": keys.get("deepseek") or os.getenv("DEEPSEEK_API_KEY"),
        "openai": keys.get("openai") or os.getenv("OPENAI_API_KEY"),
        "anthropic": keys.get("anthropic") or os.getenv("ANTHROPIC_API_KEY"),
    }
    try:
        from llm import _call_deepseek, _call_ollama, _call_openai, _call_anthropic
        result = None
        used = None  # which provider actually produced the questions

        def _api_call():
            nonlocal used
            if provider == "openai" or (provider == "auto" and keys.get("openai")):
                used = "openai"
                return _call_openai(prompt, api_key=keys.get("openai"), timeout=30)
            if provider == "anthropic" or (provider == "auto" and keys.get("anthropic")):
                used = "anthropic"
                return _call_anthropic(prompt, api_key=keys.get("anthropic"), timeout=30)
            used = "deepseek"
            return _call_deepseek(prompt, api_key=keys.get("deepseek"), timeout=30)

        if provider == "local":
            used = "local"
            result = _call_ollama("mistral", prompt, timeout=120, num_predict=160)
        else:
            result = _api_call()
            # v10.9: fall back to local whenever the API produced nothing —
            # not only in "auto". Previously, choosing "deepseek" with no
            # DEEPSEEK_API_KEY in the backend env returned [] silently and
            # generated NO FAQ. Now any empty API result falls back to
            # local so ingestion still produces questions.
            if not result or not result.get("text"):
                logger.warning(f"doc2query: provider '{provider}' returned nothing "
                               f"(missing key or API error) — falling back to local")
                used = f"{provider}->local (fallback)"
                result = _call_ollama("mistral", prompt, timeout=120, num_predict=160)

        # Log the ACTUAL provider once per file (module-level flag) so you
        # can verify ingest is really going through DeepSeek and not
        # silently using local. See _log_ingest_provider().
        _log_ingest_provider(used)

        if not result or not result.get("text"):
            return []
        questions = []
        _q_words = ("what", "how", "when", "where", "which", "who", "why",
                    "can", "does", "do", "is", "are", "will")
        for line in result["text"].splitlines():
            line = line.strip().lstrip("-•*0123456789. ").strip()
            if not line or not (4 <= len(line.split()) <= 25):
                continue
            # v10.9: accept a trailing "?" OR a clear question opener, so
            # well-formed questions the model didn't punctuate aren't all
            # discarded (a cause of empty FAQ even when generation worked).
            if line.endswith("?"):
                questions.append(line)
            elif line.split()[0].lower() in _q_words:
                questions.append(line if line.endswith("?") else line + "?")
        return questions[:n]
    except Exception as e:
        logger.warning(f"doc2query generation failed (non-fatal): {e}")
        return []


_LAST_LOGGED_PROVIDER = None


def _log_ingest_provider(used: str | None):
    """Log the ingest provider once when it changes, so it's verifiable in
    the backend logs (INFO:ingest:doc2query provider = deepseek)."""
    global _LAST_LOGGED_PROVIDER
    if used and used != _LAST_LOGGED_PROVIDER:
        _LAST_LOGGED_PROVIDER = used
        logger.info(f"doc2query provider = {used}")


def ingest_file(content: bytes, filename: str,
                api_keys: dict | None = None,
                progress=None,
                category_key: str | None = None,
                product_key: str | None = None) -> int:
    """
    Parse, chunk, embed and store a file.

    Returns the number of chunks added (0 if duplicate or empty).
    """
    collection = get_collection()

    # ── Duplicate check ───────────────────────────────────────────────────────
    existing = collection.get(where={"source": filename})
    if existing and existing.get("ids"):
        logger.info(f"Skipping duplicate: {filename}")
        return 0

    # ── Save to temp file for parsing ────────────────────────────────────────
    # Use only the extension as suffix so extract_text() can detect the type
    suffix  = os.path.splitext(filename)[1]
    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        # ── Extract ───────────────────────────────────────────────────────────
        text = extract_text(tmp_path)
        if not text or not text.strip():
            logger.warning(f"No text extracted from '{filename}'")
            return 0

        # ── Chunk ─────────────────────────────────────────────────────────────
        # Enrich each chunk with a document + section breadcrumb so retrieval
        # can tell near-identical sections apart (e.g. app-login credentials
        # vs. device-registration API credentials). See the block comment at
        # the top of this file.
        chunks = _enrich_chunks(chunk_text(text), filename)
        texts  = [c for c in chunks if c.strip()]
        if not texts:
            logger.warning(f"No usable chunks from '{filename}'")
            return 0

        # ── Embed ─────────────────────────────────────────────────────────────
        vectors = embed_texts(texts)

        # ── Store ─────────────────────────────────────────────────────────────
        # v2.1: product key(s) this file belongs to, comma-joined for
        # metadata (many-to-many). Empty string if unmapped — still
        # searchable via whole-corpus / "all".
        # v10.5: DIRECT tagging. The category/product come from where the
        # admin uploaded the doc — no filename guessing. Stored on every
        # chunk so retrieval filters on the explicit assignment.
        _cat_tag = category_key or ""
        _prod_tag = product_key or ""
        ids = [f"{filename}_{i}" for i in range(len(texts))]

        collection.add(
            documents=texts,
            embeddings=[v.tolist() for v in vectors],
            metadatas=[{"source": filename, "kind": "chunk",
                        "products": _prod_tag,
                        "category": _cat_tag} for _ in texts],
            ids=ids,
        )

        # ── doc2query entries (v8.5 / Phase 1) ───────────────────────────────
        # Generated questions are stored as their own embedded documents with
        # kind="query" and the parent chunk's id + full text in metadata.
        # retrieval_db maps query-hits back to parent text and dedupes, so
        # the answering pipeline only ever sees real chunk content.
        q_docs, q_ids, q_metas = [], [], []
        total = len(texts)
        for i, chunk in enumerate(texts):
            if progress:
                progress(stage="doc2query", done=i, total=total)
            for j, question in enumerate(_generate_questions(chunk, api_keys=api_keys)):
                q_docs.append(question)
                q_ids.append(f"{filename}_{i}_q{j}")
                q_metas.append({
                    "source": filename,
                    "kind": "query",
                    "products": _prod_tag,
                    "category": _cat_tag,
                    "parent_id": ids[i],
                    "parent_text": chunk,
                })
        if q_docs:
            q_vectors = embed_texts(q_docs)
            collection.add(
                documents=q_docs,
                embeddings=[v.tolist() for v in q_vectors],
                metadatas=q_metas,
                ids=q_ids,
            )
            logger.info(f"doc2query: added {len(q_docs)} question entries for '{filename}'")
            # v10.15: doc2query still builds RETRIEVAL entries above (they
            # improve search recall), but it NO LONGER writes to the FAQ
            # store. FAQ is now created deliberately by the admin via
            # "Generate FAQ" (browser-side). Auto-writing here overwrote
            # curated FAQ on every re-ingest (record_questions replaces a
            # source's entries) — the "my questions vanished and got
            # replaced by loads of random ones on restart" bug. The FAQ
            # store is now owned solely by the admin action.
        else:
            # v10.9: make silent empty-FAQ loud. If DOC2QUERY is on but no
            # questions were produced, the usual cause is an API provider
            # chosen with no key in the backend env (now falls back to
            # local — so if this still logs, local generation itself
            # produced nothing: check Ollama is up and mistral is pulled).
            _d2q = os.getenv("DOC2QUERY", "on").strip().lower()
            if _d2q in ("off", "0", "false"):
                logger.warning(f"doc2query: DISABLED (DOC2QUERY={_d2q}) — no FAQ for '{filename}'")
            else:
                logger.warning(f"doc2query: 0 questions generated for '{filename}' — "
                               f"check INGEST_PROVIDER + its API key, or that Ollama/"
                               f"mistral is available for local generation")

        logger.info(f"Ingested '{filename}': {len(texts)} chunks")
        return len(texts)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
