import os
import re
import logging
import tempfile
from datetime import datetime, timezone

from parsing import extract_pages, extract_pages_report
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
# Index the text OCR'd out of a document's figures (pin names, dimension
# labels) as its own chunks, tagged ocr=True. OFF until the eval says it
# helps: a figure chunk is short and label-dense, so it can outscore the
# prose chunk that actually answers, and it is guessed text competing with
# typed text. index_figure_text() adds them to a document already held.
FIGURE_TEXT_INDEX = os.getenv("FIGURE_TEXT_INDEX", "0") == "1"
from embeddings import embed_texts
from db import get_collection, invalidate_retrieval_cache

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


_FOOTER_LINE = re.compile(r"^(?P<title>.{3,70}?)\s*[–—-]\s*\d{1,4}\s*$")


def _strip_running_footer(text: str, filename: str) -> str:
    """Remove the manual's own running footer from a page.

    Every page of these manuals ends "NV200S Range User Manual – 107". It is
    not content, and it does two kinds of damage:

      * a chunk that holds nothing else becomes a chunk whose entire body is
        a page footer. 64 of 1749 chunks (3.7%) are exactly that, and they
        are not inert -- one of them ("[... — WR00147 - SMART Payout to NV200
        Adapter] NV200S Range User Manual – 107") reranked #1 at 0.9974 for
        "can the NV200 be used with a SMART Payout?", winning on the heading
        alone and then carrying no answer;
      * on every other chunk it is a tail of title words that the embedder
        and BM25 both see, making chunks from one manual look more alike.

    Conservative on purpose: a line is only a footer if it ends in a number
    AND its words are mostly the document's own title. A page whose last line
    happens to be "Supply Voltage - 24" keeps it, because "supply voltage" is
    nothing like the filename.
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    title_words = {w for w in re.findall(r"[a-z0-9]+", stem.lower())
                   if len(w) > 2}
    if not title_words:
        return text
    out = []
    for line in (text or "").split("\n"):
        m = _FOOTER_LINE.match(line.strip())
        if m:
            words = {w for w in re.findall(r"[a-z0-9]+", m.group("title").lower())
                     if len(w) > 2}
            # Most of the line's words are title words -> it is the footer.
            if words and len(words & title_words) >= max(1, int(len(words) * 0.6)):
                continue
        out.append(line)
    return "\n".join(out)


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


def _dedupe_enabled() -> bool:
    """Operator switch (console > Advanced). Defaults on; a broken policy
    file must not silently change how documents are indexed."""
    try:
        import policy
        v = policy.value("dedupe_shadowed_chunks")
        return True if v is None else bool(v)
    except Exception:
        return True


def _shingles(s: str, n: int = 3) -> set:
    """Word trigrams of `s`, lowercased. Comparing these rather than raw
    substrings is deliberate: the two copies of an introducing sentence are
    not byte-identical. pdfplumber's prose pass and its table-title pass
    disagree on leading articles and stray spacing -- observed as "The NV9
    Spectral has..." against "NV9 Spectral has...", which defeats a plain
    `in` test while being obviously the same sentence."""
    w = "".join(c if c.isalnum() or c.isspace() else " "
                for c in (s or "").lower()).split()
    if len(w) < n:
        return {" ".join(w)} if w else set()
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


CONTAINED_AT = 0.90     # share of the short chunk's trigrams the long one
                        # must already hold before the short one is dropped


def _drop_shadowed(texts: list[str], pageno: list[int], sections: list[str],
                   filename: str = "") -> tuple[list[str], list[int], list[str]]:
    """Drop a chunk whose entire text already sits inside another chunk from
    the same page.

    WHY. When a page introduces a table, pdfplumber hands the introducing
    sentence back twice: once as prose (it falls outside the table's bbox)
    and again as the rendered table's title. The prose-only chunk is then a
    strict subset of the prose+table chunk -- same section heading, same
    opening sentence, none of the rows.

    That short chunk is not merely redundant, it is a decoy. It is dense with
    the words a question about the table uses ("flash", "codes", "error") and
    carries no answer, so it outranks the chunk that does. Measured on the
    NV9 Spectral flash-code table, the empty chunk ranked #1 and the chunk
    holding "1 long 1 short -> Note path open" ranked #3; the model cited the
    right page and then correctly said it could not see the answer.

    Nothing is lost by dropping it: every word it held is still in the chunk
    that shadows it, alongside the rows.
    """
    shing = [_shingles(t) for t in texts]
    by_page: dict[int, list[int]] = {}
    for i, p in enumerate(pageno):
        by_page.setdefault(p, []).append(i)

    keep, dropped = [], 0
    for i, p in enumerate(pageno):
        me = shing[i]
        if not me:
            keep.append(i)
            continue
        shadowed = False
        for j in by_page.get(p, ()):
            # Only a STRICTLY longer chunk may shadow a shorter one. Without
            # the length test a pair of identical chunks would each shadow
            # the other and both would vanish.
            if j == i or len(texts[j]) <= len(texts[i]):
                continue
            if len(me & shing[j]) / len(me) >= CONTAINED_AT:
                shadowed = True
                break
        if shadowed:
            dropped += 1
        else:
            keep.append(i)

    if dropped:
        logger.info("%s: dropped %d chunk(s) wholly contained in another on "
                    "the same page", filename or "document", dropped)
    return ([texts[i] for i in keep], [pageno[i] for i in keep],
            [sections[i] for i in keep])


def ingest_file(content: bytes, filename: str,
                api_keys: dict | None = None,  # accepted for call-site compat; unused since doc2query removal (v10.16)
                progress=None,
                category_key: str | None = None,
                product_key: str | None = None,
                replace_existing: bool = False,
                report: dict | None = None) -> int:
    """
    Parse, chunk, embed and store a file.

    Returns the number of chunks added (0 if duplicate or empty).

    `report`, if given, is filled in with what the parser could not read:
    {"pages": total, "ocr_candidates": [page numbers]}. Those are the pages
    with no text layer or an unusable one; they are NOT indexed here. The
    upload route hands the list to the console, which offers them for OCR
    (see ingest_ocr_pages) rather than running it unasked -- OCR is a guess
    from the picture and the admin should choose to accept that.
    """
    collection = get_collection()

    # The caller passes this so the console can show what is happening; it was
    # accepted and never called, so /upload/status reported the 0.0 it was
    # created with until the job finished. Every upload read "working 0%" for
    # its whole run -- on a 200-page manual that is minutes of a progress
    # indicator that looks stuck.
    def _step(stage, done=0, total=0):
        if progress:
            try:
                progress(stage, done, total)
            except Exception:      # a broken reporter must not fail an ingest
                pass

    # v12.0: keep the ORIGINAL file so answers can offer a download link
    # back to the source document. Previously the upload lived only in a
    # temp file that was deleted after parsing, so there was nothing to
    # link to. Stored under SOURCE_FILE_DIR (on the persistent volume in
    # Docker) keyed by filename, matching the chunk metadata "source".
    # v15: resolved by docstore, which defaults to <repo>/documents rather
    # than the Docker path "/data/source_files" -- that default silently
    # resolved to C:\data\source_files on Windows, outside the repo and any
    # backup, and was mistaken for data loss.
    # ── Duplicate/version check ───────────────────────────────────────────────
    content_hash = docstore.sha256(content)
    existing = collection.get(where={"source": filename}, include=["metadatas"])
    old_ids = list((existing or {}).get("ids") or [])
    old_versions = {
        (m or {}).get("document_version") or (m or {}).get("content_sha256")
        for m in ((existing or {}).get("metadatas") or [])
    }
    if old_ids and content_hash in old_versions:
        logger.info("Skipping unchanged document: %s (%s)", filename,
                    content_hash[:12])
        return 0
    if old_ids and not replace_existing:
        logger.info(f"Skipping duplicate: {filename}")
        return 0

    previous_content = None
    if old_ids:
        previous_path = docstore.find(filename)
        if previous_path:
            try:
                with open(previous_path, "rb") as fh:
                    previous_content = fh.read()
            except Exception as exc:
                logger.warning("could not stage the previous original for "
                               "replacement rollback: %s", exc)

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
        _step("reading the document", 0, 1)
        pages, parsed = extract_pages_report(tmp_path)
        ocr_candidates = sorted(set(parsed.get("empty", []))
                                | set(parsed.get("garbled", [])))
        if report is not None:
            report["pages"] = parsed.get("pages", len(pages))
            report["ocr_candidates"] = ocr_candidates
        if not pages:
            logger.warning(f"No text extracted from '{filename}'")
            # Nothing indexed, but the pages are still worth OCR -- a fully
            # scanned manual is the case that most needs it. Keep the
            # original so the OCR step has something to read, and record
            # the candidates so the offer survives the upload card.
            if ocr_candidates:
                docstore.save(filename, content)
                docstore.record(filename, content=content, chunks=0,
                                pages=parsed.get("pages", len(ocr_candidates)),
                                settings=docstore.current_settings(),
                                ocr={"candidates": ocr_candidates, "done": []})
            return 0

        # ── Figures ──────────────────────────────────────────────────────────
        # The pictures on each page, cut out now and attached to that page's
        # chunks by name, so an answer can show the diagram beside the text
        # it cites. Attachment is by page, not by content: nothing here is
        # searchable, so retrieval is untouched. Never fails the ingest.
        fig_by_page: dict[int, list[str]] = {}
        if suffix.lower() == ".pdf":
            _step("cutting out figures", 0, 1)
            try:
                import figures as _figures
                fig_by_page = _figures.extract_figures(
                    tmp_path, filename,
                    progress=lambda d, t: _step("cutting out figures", d, t))
            except Exception as exc:
                logger.warning("figure extraction failed for %r (answers will "
                               "show no pictures from it): %s", filename, exc)
        fig_chunks: list[tuple[int, str, str, float]] = []   # (page, name, text, conf)
        if FIGURE_TEXT_INDEX and fig_by_page:
            _step("reading text in figures", 0, 1)
            try:
                import figures as _figures
                read = _figures.figure_text(
                    tmp_path, filename,
                    progress=lambda d, t: _step("reading text in figures", d, t))
                fig_chunks = _figure_chunks(filename, read)
            except Exception as exc:
                logger.warning("figure text skipped for %r: %s", filename, exc)

        # ── Chunk ─────────────────────────────────────────────────────────────
        # Enrich each chunk with a document + section breadcrumb so retrieval
        # can tell near-identical sections apart (e.g. app-login credentials
        # vs. device-registration API credentials). See the block comment at
        # the top of this file.
        _step("splitting into sections", 0, len(pages))
        texts, pageno, sections = [], [], []
        for _pi, (pno, ptext) in enumerate(pages, start=1):
            if _pi % 5 == 0 or _pi == len(pages):
                _step("splitting into sections", _pi, len(pages))
            if not ptext or not ptext.strip():
                continue
            # Before chunking, so the footer never reaches an embedding and
            # cannot become a chunk's entire body.
            ptext = _strip_running_footer(ptext, filename)
            if not ptext.strip():
                continue
            for raw in chunk_text(ptext, size=CHUNK_SIZE,
                                  overlap=CHUNK_OVERLAP):
                # The heading the chunker attached, taken off BEFORE
                # enrichment so it can be stored as metadata rather than
                # only embedded in the text. That distinction is the point
                # of the change: as metadata it can be filtered and boosted.
                body, section = pop_section(raw)
                # Tested on the BODY, not on the enriched chunk. The check
                # below runs after _enrich_chunks has prepended a breadcrumb,
                # so "[Doc — Section]\n" is always non-empty and a chunk with
                # nothing in it would be stored as though it had content.
                if not body.strip():
                    continue
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

        # A table that runs onto the next page restarts there with no
        # heading and no column names; merged cells arrive blank; a matrix
        # reads wrongly. See tables.py for the measurements.
        import tables as _tables
        texts, sections = _tables.carry_table_context(
            texts, sections, os.path.splitext(filename)[0])
        texts = [_tables.spell_out(t) for t in texts]

        if _dedupe_enabled():
            texts, pageno, sections = _drop_shadowed(texts, pageno, sections,
                                                     filename)

        # Figure-text chunks go in AFTER table carry and dedupe: they are
        # not prose, and a pinout photo's "1 2 15 16" must not be
        # "shadowed" away by another pinout photo's identical labels.
        extra_meta: list[dict] = [{} for _ in texts]
        for pno, name, body, conf in fig_chunks:
            texts.append(body)
            pageno.append(pno)
            sections.append("")
            extra_meta.append({"ocr": True, "ocr_confidence": conf,
                               "figure_text": True, "figures": name})

        # ── Embed ─────────────────────────────────────────────────────────────
        # The long pole on a big document, and the reason the console needs to
        # say something: a 235-chunk manual spends most of its ingest here.
        # In batches, so the percentage MOVES. One embed_texts call over 200
        # chunks is a single minutes-long step that can only report 0% -- and
        # a progress figure that never changes is indistinguishable from a
        # stalled job, which is the one thing a progress bar exists to rule
        # out.
        _step("understanding the text", 0, len(texts))
        vectors = []
        _batch = 16
        for _i in range(0, len(texts), _batch):
            vectors.extend(embed_texts(texts[_i:_i + _batch]))
            _step("understanding the text", min(_i + _batch, len(texts)),
                  len(texts))
        _step("saving", len(texts), len(texts))

        # ── Store ─────────────────────────────────────────────────────────────
        # v12.0: product key(s) this file belongs to, comma-joined for
        # metadata (many-to-many). Empty string if unmapped — still
        # searchable via whole-corpus / "all".
        # v10.5: DIRECT tagging. The category/product come from where the
        # admin uploaded the doc — no filename guessing. Stored on every
        # chunk so retrieval filters on the explicit assignment.
        _cat_tag = category_key or ""
        _prod_tag = product_key or ""
        # IDs include the immutable content version. This lets a replacement
        # be written completely before the old IDs are removed, so an embed or
        # database failure cannot erase the working version first.
        ids = [f"{filename}:{content_hash[:16]}:{i}" for i in range(len(texts))]
        indexed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

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
                        "document_version": content_hash,
                        "content_sha256": content_hash,
                        "indexed_at": indexed_at,
                        # One flag per product this document belongs to, so a
                        # document can belong to SEVERAL. Chroma's `where` is
                        # exact-match, so a comma-joined "a,b" matches neither
                        # "a" nor "b" and multi-tagging would have silently
                        # made a document invisible to both products. A flag
                        # per key keeps the filter server-side and exact.
                        **{("prod_" + k): True
                           for k in _prod_tag.split(",") if k.strip()},
                        # v12.0: page number for citation + deep-linking.
                        "page": pageno[i],
                        # Crops from this page (figures.py), comma-joined:
                        # Chroma metadata takes no lists.
                        "figures": ",".join(fig_by_page.get(pageno[i], [])),
                        # The document's own heading for this chunk, found by
                        # layout rather than by matching against a list of
                        # known titles. Stored so it can be FILTERED and
                        # boosted, not just embedded -- "more on this" can
                        # then mean "more from the same section", and a spec
                        # question can prefer the section that names it.
                        "section": sections[i],
                        # A figure-text chunk overrides `figures` with the
                        # one figure it was read from, and carries ocr=True.
                        **extra_meta[i]}
                       for i in range(len(texts))],
            ids=ids,
        )

        # The new version is queryable now. Commit the durable original with
        # an atomic replace, then retire the previous chunks. If retaining the
        # source fails, roll the new IDs back and leave the old index intact.
        try:
            docstore.save(filename, content)
        except Exception:
            collection.delete(ids=ids)
            raise

        if old_ids:
            try:
                collection.delete(ids=old_ids)
            except Exception:
                # Prefer a duplicate index over data loss, but do not report a
                # successful replacement: the operator needs to retry/repair.
                try:
                    collection.delete(ids=ids)
                finally:
                    if previous_content is not None:
                        try:
                            docstore.save(filename, previous_content)
                        except Exception as restore_exc:
                            logger.critical("could not restore original %r after "
                                            "index replacement failed: %s",
                                            filename, restore_exc)
                    raise

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
                            pages=parsed.get("pages", len(pages)),
                            settings=docstore.current_settings(),
                            # A new version starts with no OCR done: its
                            # OCR chunks (if any) went with old_ids above.
                            ocr={"candidates": ocr_candidates, "done": []})
        except Exception as _exc:
            logger.warning(f"Could not record manifest entry: {_exc}")

        # A replacement may have exactly the same number of chunks. BM25's
        # normal count-based refresh cannot detect that content change.
        invalidate_retrieval_cache()

        logger.info(f"Ingested '{filename}': {len(texts)} chunks")
        return len(texts)

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def ocr_chunks_for(filename: str, pages: list[int] | None = None) -> list[str]:
    """IDs of the OCR chunks a document already holds, optionally only for
    the given pages. Metadata-only read, so cheap on a large collection."""
    collection = get_collection()
    got = collection.get(where={"source": filename}, include=["metadatas"]) or {}
    wanted = {int(p) for p in pages} if pages else None
    return [cid for cid, m in zip(got.get("ids") or [], got.get("metadatas") or [])
            if (m or {}).get("ocr")
            and (wanted is None or (m or {}).get("page") in wanted)]


def ingest_ocr_pages(filename: str, pages: list[int], progress=None) -> int:
    """Read the given pages of a retained PDF by OCR and index the result.

    The admin-approved half of the OCR flow. ingest_file() reported these
    pages as unreadable and indexed nothing for them; this reads them from
    their pixels and adds chunks tagged ocr=True, with the page number set
    so they cite like any other chunk and the citation can say "(OCR)".

    Re-runnable: OCR chunks already held for these pages are replaced, so
    approving a page twice does not index it twice. The original's version
    hash is taken from the manifest so the new IDs sit under the same
    document version as the text chunks and are retired with them when a
    new version is uploaded.

    Returns the number of chunks added. The section metadata is left empty:
    OCR gives no font data, so there is no heading detection to fill it.
    """
    import ocr as _ocr

    path = docstore.find(filename)
    if not path:
        raise FileNotFoundError(f"no retained original for {filename!r}")
    if not path.lower().endswith(".pdf"):
        raise ValueError("OCR applies to PDF documents only")
    pages = sorted({int(p) for p in pages if int(p) >= 1})
    if not pages:
        return 0

    def _step(stage, done=0, total=0):
        if progress:
            try:
                progress(stage, done, total)
            except Exception:
                pass

    entry = docstore.load_manifest()["documents"].get(
        docstore._safe_basename(filename)) or {}
    content_hash = entry.get("sha256")
    if not content_hash:
        with open(path, "rb") as fh:
            content_hash = docstore.sha256(fh.read())
    scope = catalog_scope_for(filename)

    _step("reading pages by OCR", 0, len(pages))
    read = _ocr.ocr_pdf_pages(
        path, pages,
        progress=lambda d, t: _step("reading pages by OCR", d, t))

    texts, pageno, confs = [], [], []
    for pno, ptext, conf in read:
        if pno not in pages:
            # Only what was asked for is replaced below; a page that was
            # not would be added beside its existing chunks.
            continue
        ptext = _strip_running_footer(ptext, filename)
        for raw in chunk_text(ptext, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
            body, _section = pop_section(raw)
            if not body.strip():
                continue
            for c in _enrich_chunks([body], filename, None):
                c = strip_table_fences(strip_section_marks(c))
                if c.strip():
                    texts.append(c)
                    pageno.append(pno)
                    confs.append(conf)

    collection = get_collection()
    # Earlier OCR chunks for these pages go FIRST, unlike a document
    # replacement, which writes the new version before retiring the old.
    # The IDs are deterministic (version, page, index) so a re-run's IDs
    # collide with the ones it replaces, and the exposure is small: the
    # admin just asked for these pages to be re-read, and a failed re-read
    # leaves them listed as pending again on the row.
    stale = ocr_chunks_for(filename, pages)
    if stale:
        collection.delete(ids=stale)
    if not texts:
        # Nothing legible on any of them. Still recorded as done, so the
        # console stops offering pages OCR has already looked at.
        _record_ocr_done(filename, pages)
        logger.warning("OCR read nothing usable on %s pages %s", filename, pages)
        return 0

    _step("understanding the text", 0, len(texts))
    vectors = []
    _batch = 16
    for _i in range(0, len(texts), _batch):
        vectors.extend(embed_texts(texts[_i:_i + _batch]))
        _step("understanding the text", min(_i + _batch, len(texts)), len(texts))
    _step("saving", len(texts), len(texts))

    _prod_tag = scope.get("product") or ""
    _cat_tag = scope.get("category") or ""
    indexed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ids = [f"{filename}:{content_hash[:16]}:ocr:{pageno[i]}:{i}"
           for i in range(len(texts))]
    try:
        import figures as _figures
        fig_by_page = _figures.figures_by_page(filename)
    except Exception:
        fig_by_page = {}
    collection.add(
        documents=texts,
        embeddings=[v.tolist() for v in vectors],
        metadatas=[{"source": filename, "kind": "chunk",
                    "product": _prod_tag, "products": _prod_tag,
                    "category": _cat_tag,
                    "document_version": content_hash,
                    "content_sha256": content_hash,
                    "indexed_at": indexed_at,
                    **{("prod_" + k): True
                       for k in _prod_tag.split(",") if k.strip()},
                    "page": pageno[i],
                    "section": "",
                    "figures": ",".join(fig_by_page.get(pageno[i], [])),
                    # The provenance flag. Read by retrieval into every
                    # result and by _build_sources into the citation label.
                    "ocr": True,
                    "ocr_confidence": confs[i]}
                   for i in range(len(texts))],
        ids=ids,
    )
    _record_ocr_done(filename, pages)
    invalidate_retrieval_cache()
    logger.info("OCR added %d chunk(s) from %d page(s) of '%s'",
                len(texts), len(read), filename)
    return len(texts)


def _figure_chunks(filename: str,
                   read: dict[str, tuple[str, float]]) -> list[tuple[int, str, str, float]]:
    """One chunk per figure with legible text: what the figure is (page,
    caption) and the labels read out of it. Returns (page, name, body, conf).

    The body names itself a figure so the model knows it is looking at
    labels, not prose: "1 2 15 16" under "connector pinout" is a pinout;
    on its own it is noise.
    """
    import figures as _figures
    meta = _figures.figures_for(filename)
    out = []
    for name, (text, conf) in read.items():
        m = meta.get(name) or {}
        try:
            pno = int(m.get("page") or 0)
        except (TypeError, ValueError):
            pno = 0
        if not pno:
            continue
        caption = (m.get("caption") or "").strip()
        head = f"Figure on page {pno}" + (f": {caption}" if caption else "")
        body = head + "\nText in the figure:\n" + text
        for c in _enrich_chunks([body], filename, f"Figure: {caption}" if caption else "Figure"):
            c = strip_table_fences(strip_section_marks(c))
            if c.strip():
                out.append((pno, name, c, conf))
    return out


def figure_text_chunks_for(filename: str) -> list[str]:
    """IDs of the figure-text chunks a document holds."""
    collection = get_collection()
    got = collection.get(where={"source": filename}, include=["metadatas"]) or {}
    return [cid for cid, m in zip(got.get("ids") or [], got.get("metadatas") or [])
            if (m or {}).get("figure_text")]


def index_figure_text(filename: str, progress=None) -> int:
    """Read and index the text in the figures of a document already held.

    The backfill for a document ingested before figure text was on, and
    the way to measure the feature without re-ingesting the corpus: the
    figures are already cut (or are cut now), the prose chunks stay as
    they are, and only the figure-text chunks are added. Replaces any it
    added before. Returns the number of chunks added.
    """
    import figures as _figures

    path = docstore.find(filename)
    if not path:
        raise FileNotFoundError(f"no retained original for {filename!r}")
    if not path.lower().endswith(".pdf"):
        return 0

    def _step(stage, done=0, total=0):
        if progress:
            try:
                progress(stage, done, total)
            except Exception:
                pass

    if not _figures.figures_for(filename):
        _step("cutting out figures", 0, 1)
        _figures.extract_figures(path, filename,
                                 progress=lambda d, t: _step("cutting out figures", d, t))
    _step("reading text in figures", 0, 1)
    read = _figures.figure_text(
        path, filename, progress=lambda d, t: _step("reading text in figures", d, t))
    chunks = _figure_chunks(filename, read)

    collection = get_collection()
    stale = figure_text_chunks_for(filename)
    if stale:
        collection.delete(ids=stale)
    if not chunks:
        return 0

    entry = docstore.load_manifest()["documents"].get(
        docstore._safe_basename(filename)) or {}
    content_hash = entry.get("sha256")
    if not content_hash:
        with open(path, "rb") as fh:
            content_hash = docstore.sha256(fh.read())
    scope = catalog_scope_for(filename)
    texts = [c[2] for c in chunks]

    _step("understanding the text", 0, len(texts))
    vectors = []
    _batch = 16
    for _i in range(0, len(texts), _batch):
        vectors.extend(embed_texts(texts[_i:_i + _batch]))
        _step("understanding the text", min(_i + _batch, len(texts)), len(texts))

    _prod_tag = scope.get("product") or ""
    _cat_tag = scope.get("category") or ""
    indexed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    collection.add(
        documents=texts,
        embeddings=[v.tolist() for v in vectors],
        metadatas=[{"source": filename, "kind": "chunk",
                    "product": _prod_tag, "products": _prod_tag,
                    "category": _cat_tag,
                    "document_version": content_hash,
                    "content_sha256": content_hash,
                    "indexed_at": indexed_at,
                    **{("prod_" + k): True
                       for k in _prod_tag.split(",") if k.strip()},
                    "page": pno, "section": "",
                    "figures": name, "figure_text": True,
                    "ocr": True, "ocr_confidence": conf}
                   for (pno, name, _body, conf) in chunks],
        ids=[f"{filename}:{content_hash[:16]}:figtext:{i}" for i in range(len(chunks))],
    )
    invalidate_retrieval_cache()
    logger.info("figure text: %d chunk(s) indexed for '%s'", len(chunks), filename)
    return len(chunks)


def catalog_scope_for(filename: str) -> dict:
    """The category/product a document is filed under, as chunk metadata
    wants them, so OCR chunks are scoped exactly like the text chunks."""
    try:
        from docindex import source_index
        for r in source_index():
            if r.get("source") == filename:
                return {"category": r.get("category") or "",
                        "product": r.get("product") or ""}
    except Exception as exc:
        logger.warning("could not read scope for %r: %s", filename, exc)
    return {}


def _record_ocr_done(filename: str, pages: list[int]) -> None:
    """Mark pages as OCR'd in the manifest. Never fails the caller."""
    try:
        entry = docstore.load_manifest()["documents"].get(
            docstore._safe_basename(filename)) or {}
        state = dict(entry.get("ocr") or {})
        done = sorted(set(state.get("done") or []) | set(pages))
        state["done"] = done
        state.setdefault("candidates", [])
        docstore.record(filename, ocr=state)
    except Exception as exc:
        logger.warning("could not record OCR pages for %r: %s", filename, exc)
