import os
import re
import logging
import tempfile

from parsing import extract_pages
import docstore
from chunking import (chunk_text, strip_table_fences, pop_section,
                      strip_section_marks)

# Chunk geometry, env-tunable so it can be swept against eval.py without code
# edits. 500/50 (chunking.py's own defaults) was too small to hold a spec table
# or a protocol list: observed splitting one mid-item, so the chunk began "and
# SI2" with the start of the list in a different chunk, and the model refused a
# question whose answer had in fact been retrieved.
#
# CHANGING THESE ONLY AFFECTS DOCUMENTS INGESTED AFTERWARDS. Existing chunks
# keep the geometry they were created with; a full re-ingest is the only way to
# apply it retroactively.
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))
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


def _enrich_chunks(chunks: list[str], filename: str,
                   section: str | None = None) -> list[str]:
    """
    Prepend "[<doc> — <section>]" (or "[<doc>]" when no header is found) to
    each chunk so section identity travels into embedding, BM25, and rerank.
    Stripped again in main.py before generation/grounding.

    `section` is the heading the chunker found by LAYOUT (font size and
    weight) and is preferred when present. SECTION_TITLES below is the older
    route: a 19-entry whitelist tuned to four documents, which covered 65 of
    684 chunks -- 9% -- once the corpus reached eleven. It stays as the
    fallback for a document whose headings carry no size or weight signal at
    all, such as the two-page CS checklist.
    """
    doc = os.path.splitext(filename)[0]
    out = []
    for c in chunks:
        crumb = section or _breadcrumb(c)
        prefix = f"[{doc} — {crumb}]" if crumb else f"[{doc}]"
        out.append(f"{prefix}\n{c}")
    return out


def ingest_file(content: bytes, filename: str,
                api_keys: dict | None = None,  # accepted for call-site compat; unused since doc2query removal (v10.16)
                progress=None,
                category_key: str | None = None,
                product_key: str | None = None) -> int:
    """
    Parse, chunk, embed and store a file.

    Returns the number of chunks added (0 if duplicate or empty).
    """
    collection = get_collection()

    # v12.0: keep the ORIGINAL file so answers can offer a download link
    # back to the source document. Previously the upload lived only in a
    # temp file that was deleted after parsing, so there was nothing to
    # link to. Stored under SOURCE_FILE_DIR (on the persistent volume in
    # Docker) keyed by filename, matching the chunk metadata "source".
    # v15: resolved by docstore, which defaults to <repo>/documents rather
    # than the Docker path "/data/source_files" -- that default silently
    # resolved to C:\data\source_files on Windows, outside the repo and any
    # backup, and was mistaken for data loss.
    _src_dir = docstore.store_dir()

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

        # ── Extract (page-aware, v12.0) ──────────────────────────────────────
        # Chunk PER PAGE rather than over one concatenated string, so every
        # chunk knows which page it came from and answers can cite it. The
        # cost is that a passage spanning a page break is split at the
        # boundary; the win is a citation the reader can actually turn to.
        # Page-crossing content is largely recovered by retrieval returning
        # both halves, and by the breadcrumb enrichment below keeping each
        # half attributable to its section.
        pages = extract_pages(tmp_path)
        if not pages:
            logger.warning(f"No text extracted from '{filename}'")
            return 0

        # ── Chunk ─────────────────────────────────────────────────────────────
        # Enrich each chunk with a document + section breadcrumb so retrieval
        # can tell near-identical sections apart (e.g. app-login credentials
        # vs. device-registration API credentials). See the block comment at
        # the top of this file.
        texts, pageno, sections = [], [], []
        for pno, ptext in pages:
            if not ptext or not ptext.strip():
                continue
            for raw in chunk_text(ptext, size=CHUNK_SIZE,
                                  overlap=CHUNK_OVERLAP):
                # The heading the chunker attached, taken off BEFORE
                # enrichment so it can be stored as metadata rather than
                # only embedded in the text. That distinction is the point
                # of the change: as metadata it can be filtered and boosted.
                body, section = pop_section(raw)
                for c in _enrich_chunks([body], filename, section):
                    # Sentinels are a chunker-internal signal only — strip
                    # before anything is embedded, BM25-tokenised or shown
                    # to a model.
                    c = strip_table_fences(strip_section_marks(c))
                    if c.strip():
                        texts.append(c)
                        pageno.append(pno)
                        sections.append(section or "")
        if not texts:
            logger.warning(f"No usable chunks from '{filename}'")
            return 0

        # ── Retain original for download ──────────────────────────────────────
        try:
            os.makedirs(_src_dir, exist_ok=True)
            with open(os.path.join(_src_dir, os.path.basename(filename)), "wb") as fh:
                fh.write(content)
        except Exception as e:
            # Non-fatal: ingestion still succeeds, the answer just won't
            # offer a download link for this source.
            logger.warning(f"could not retain source file for '{filename}': {e}")

        # ── Embed ─────────────────────────────────────────────────────────────
        vectors = embed_texts(texts)

        # ── Store ─────────────────────────────────────────────────────────────
        # v12.0: product key(s) this file belongs to, comma-joined for
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
            # BOTH spellings, deliberately. ingest wrote "products" (plural)
            # while retrieval_db._matches_scope and the Chroma `where` filter
            # read "product" (singular), so a freshly ingested document was
            # invisible to every product-scoped query — it answered only
            # unscoped, which reads as "the document I just added doesn't
            # work". diag_scope.py --fix existed to repair this by hand after
            # the fact; writing both keys here means a rebuild comes out
            # correct with no repair step, which matters now that reindex.py
            # makes rebuilding routine. They always hold the same value.
            metadatas=[{"source": filename, "kind": "chunk",
                        "product": _prod_tag,
                        "products": _prod_tag,
                        "category": _cat_tag,
                        # v12.0: page number for citation + deep-linking.
                        "page": pageno[i],
                        # The document's own heading for this chunk, found by
                        # layout rather than by matching against a list of
                        # known titles. Stored so it can be FILTERED and
                        # boosted, not just embedded -- "more on this" can
                        # then mean "more from the same section", and a spec
                        # question can prefer the section that names it.
                        "section": sections[i]}
                       for i in range(len(texts))],
            ids=ids,
        )

        # v10.16: doc2query removed. It generated synthetic per-chunk
        # questions (kind="query") purely to boost retrieval recall; the
        # hybrid BM25+dense/RRF retriever, breadcrumb enrichment and the
        # cross-encoder reranker now cover that, so the extra ingest-time
        # LLM call and ~4x vector-count inflation were no longer earning
        # their keep. The FAQ store is unaffected — it has been admin-
        # curated (not doc2query-fed) since v10.15.

        # v15: record HOW this document was ingested. Chunk geometry only
        # applies to documents ingested afterwards, so without this the index
        # quietly holds a mix of geometries and "did the chunking change help?"
        # cannot be answered. Bookkeeping never fails the ingest.
        try:
            docstore.record(filename, content=content, chunks=len(texts),
                            pages=len(pages), settings=docstore.current_settings())
        except Exception as _exc:
            logger.warning(f"Could not record manifest entry: {_exc}")

        logger.info(f"Ingested '{filename}': {len(texts)} chunks")
        return len(texts)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
