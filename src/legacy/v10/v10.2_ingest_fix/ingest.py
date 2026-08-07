import os
import re
import logging
import tempfile

from parsing import extract_text
from chunking import chunk_text
from embeddings import embed_texts
from db import get_collection
from products import product_for_source

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
    try:
        from llm import _call_deepseek, _call_ollama, _call_openai, _call_anthropic
        result = None

        def _api_call():
            if provider == "openai" or (provider == "auto" and keys.get("openai")):
                return _call_openai(prompt, api_key=keys.get("openai"), timeout=30)
            if provider == "anthropic" or (provider == "auto" and keys.get("anthropic")):
                return _call_anthropic(prompt, api_key=keys.get("anthropic"), timeout=30)
            # default API is deepseek
            return _call_deepseek(prompt, api_key=keys.get("deepseek"), timeout=30)

        if provider == "local":
            result = _call_ollama("mistral", prompt, timeout=120, num_predict=160)
        else:
            result = _api_call()
            if (not result or not result.get("text")) and provider == "auto":
                # auto: fall back to local only if no API answered
                result = _call_ollama("mistral", prompt, timeout=120, num_predict=160)

        if not result or not result.get("text"):
            return []
        questions = []
        for line in result["text"].splitlines():
            line = line.strip().lstrip("-•*0123456789. ").strip()
            if line and line.endswith("?") and 4 <= len(line.split()) <= 25:
                questions.append(line)
        return questions[:n]
    except Exception as e:
        logger.warning(f"doc2query generation failed (non-fatal): {e}")
        return []


def ingest_file(content: bytes, filename: str,
                api_keys: dict | None = None,
                progress=None) -> int:
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
        _prod_tag = ",".join(product_for_source(filename))
        ids = [f"{filename}_{i}" for i in range(len(texts))]

        collection.add(
            documents=texts,
            embeddings=[v.tolist() for v in vectors],
            metadatas=[{"source": filename, "kind": "chunk",
                        "products": _prod_tag} for _ in texts],
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

        logger.info(f"Ingested '{filename}': {len(texts)} chunks")
        return len(texts)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
