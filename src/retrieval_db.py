"""
Hybrid retrieval: BM25 (full corpus) + dense (ChromaDB) merged via RRF.

Each ranking is computed INDEPENDENTLY over the full corpus, so a chunk
that's a strong keyword match but a weak embedding match (or vice versa)
can still surface — a chunk ranking #1 on BM25 but absent from dense
results entirely is not invisible to the merge.

CAVEAT, and it is a big one (2026-08-28): "not invisible" is not the same
as "competitive". RRF rewards agreement, so a chunk only ONE ranking finds
scores at most 1/(RRF_K+1) and routinely loses to chunks both rankings rank
mediocrely. Surfacing it therefore depends on the candidate window being
wide enough to carry it to the reranker — see RETRIEVAL_CANDIDATE_MARGIN.
For years the window was exactly top_k and this docstring's promise did not
hold in practice.
"""

import logging
import os
import re
import threading
from rank_bm25 import BM25Okapi

from embeddings import embed_query
from db import get_collection
from text_utils import rrf_merge

logger = logging.getLogger(__name__)

RRF_K = 60

# How many candidates reach the reranker, as a multiple of top_k. 1.0 is the
# pre-2026-08-28 behaviour (reranker saw only top_k); 2.0 hands it the full
# margin retrieve_from_db already computes. See the note at the cut itself.
# DEFAULT 1.0 ON EVIDENCE, not on principle. Widening the window to 2.0 was
# the obvious fix for the single-list problem below and it MEASURED BADLY:
# over the 34-case suite forced through retrieval it lost two cases, gained
# none, and dropped mean grounding 0.9934 -> 0.9539. Sixteen extra mediocre
# candidates displace good ones when the reranker picks CONTEXT_K. Kept as a
# knob so the experiment is repeatable, but do not raise it without
# re-running that comparison.
RETRIEVAL_CANDIDATE_MARGIN = float(os.getenv("RETRIEVAL_CANDIDATE_MARGIN", "1.0"))

# Guarantee the top N of EACH individual ranking reaches the reranker, even
# when RRF buried them for being found by only one retriever. 0 disables.
#
# This is the fix that measured well, because it is surgical. Across the 51
# questions in both suites it rescued a candidate on 12 of them, at an
# average of 0.45 extra candidates per query, and changed the final answer
# on exactly ONE -- the query that was broken (rerank top 0.216 -> 0.993).
# End to end: no regressions on either suite, grounding and latency flat.
RETRIEVAL_ARM_GUARANTEE = int(os.getenv("RETRIEVAL_ARM_GUARANTEE", "3"))

_bm25_lock = threading.Lock()
_bm25_cache = {"count": -1, "index": None, "chunks": None}


def _invalidate_bm25_cache():
    """Force BM25 rebuild on next query. Needed after a re-tag/reassign,
    where chunk COUNT is unchanged so the count-based cache wouldn't
    otherwise refresh the metadata it holds."""
    _bm25_cache["count"] = -1



# BM25 tokenisation, shared by the corpus and the query -- and it has to be
# shared, because for years it was not.
#
# Both sides used `text.lower().split()`, a bare whitespace split, so a
# token kept whatever punctuation touched it. The query "Can I connect to
# MyCheckr using linux?" therefore asked BM25 for the term `linux?`, which
# appears nowhere in the corpus:
#
#     get_scores(["linux"])   max 9.5688   top 3 = the Linux document
#     get_scores(["linux?"])  max 0.0000   not in the idf table at all
#
# An unseen term contributes nothing, so the question silently became
# "can i connect to mycheckr using" and returned MyCheckr manuals. With
# the term intact it wins easily -- idf("linux") is 5.018 against
# idf("mycheckr") 2.233, exactly as it should be, because the rare word is
# the one carrying the question.
#
# This was never specific to Linux. The last word of a question is both
# the one that collects the "?" and, very often, the most specific term in
# it: "does it support ccTalk?", "what is the weight of the NV200S?". The
# same applies on the corpus side, where "environment:" and "environment"
# were two different terms. Found 2026-09-22 from a MyCheckr/Linux
# ranking complaint that turned out not to be about ranking.
#
# Only LEADING and TRAILING punctuation is stripped, and "+" is kept
# everywhere. Internal structure is load-bearing in this corpus:
# "nv9usb+" must not collapse into "nv9usb" (they are different products),
# "192.168.137.8" must stay one token, and "linux-based" must not become
# two. Stripping to \w+ would do all three.
# Kept, but not as a regex over the token. The first version was
#   _BM25_EDGE = re.compile(r"^[^\w+]+|[^\w+]+$")
# and CodeQL was right about it: the `[^\w+]+$` alternative is anchored at
# the END, so on a token that is a long run of punctuation and never
# matches, the engine retries from every start position -- quadratic in the
# token length, on a string that comes straight from the visitor's query.
# Nobody would type it, which is exactly why it is worth fixing: this
# parses UNCONTROLLED input, and "nobody would type it" is not a property
# of an attacker.
#
# A two-ended scan is linear, has no backtracking to reason about, and says
# plainly what it keeps: alphanumerics, "_" and "+" at the edges, and
# anything at all in the middle (see the token tests -- "nv9usb+",
# "192.168.137.8" and "linux-based" all have to survive whole).
def _strip_token_edges(word: str) -> str:
    i, j = 0, len(word)
    while i < j and not (word[i].isalnum() or word[i] in "_+"):
        i += 1
    while j > i and not (word[j - 1].isalnum() or word[j - 1] in "_+"):
        j -= 1
    return word[i:j]


def _bm25_tokens(text: str) -> list[str]:
    """Lowercase whitespace tokens with edge punctuation removed."""
    out = []
    for word in (text or "").lower().split():
        word = _strip_token_edges(word)
        if word:
            out.append(word)
    return out


def _get_bm25_index(collection):
    count = collection.count()

    with _bm25_lock:
        if _bm25_cache["count"] == count and _bm25_cache["index"] is not None:
            return _bm25_cache["index"], _bm25_cache["chunks"]

        data = collection.get(include=["documents", "metadatas"])
        chunks = [
            {
                "id": i,
                "text": d,
                "source": m.get("source", "unknown"),
                # v12.0: carried through so answers can cite a page.
                "page": m.get("page"),
                "product": m.get("product", ""),
                "category": m.get("category", ""),
                "section": m.get("section", ""),
            }
            for i, d, m in zip(data["ids"], data["documents"], data["metadatas"])
            # v10.16: doc2query removed. Fresh ingests no longer create
            # kind="query" entries, but a collection built before this
            # change may still hold them. Drop them here so leftover
            # synthetic questions never enter BM25 or surface as results;
            # a re-ingest clears them permanently.
            if m.get("kind", "chunk") != "query"
        ]

        corpus = [_bm25_tokens(c["text"]) for c in chunks]
        index = BM25Okapi(corpus) if corpus else None

        _bm25_cache["count"] = count
        _bm25_cache["index"] = index
        _bm25_cache["chunks"] = chunks

        return index, chunks


def _matches_scope(meta: dict, source_filter: str | None,
                   scope: dict | None) -> bool:
    """v10.5: scope by EXPLICIT tags set at upload — no filename guessing.
    scope = {"product": key} matches chunks tagged with that product;
    scope = {"category": key} matches any chunk in that category. The
    source_filter still supports the single-document 'ask about this doc'
    flow. None scope = unscoped (whole corpus)."""
    if source_filter and meta.get("source") != source_filter:
        return False
    if scope:
        if "product" in scope:
            key = scope["product"]
            # A document can be tagged to several products, stored as a flag
            # per key. The comma-split covers the same thing for chunks
            # indexed before the flags existed, so neither needs a reindex.
            if meta.get("prod_" + key):
                return True
            tagged = (meta.get("products") or meta.get("product") or "")
            return key in [t.strip() for t in tagged.split(",") if t.strip()]
        if "category" in scope:
            return meta.get("category") == scope["category"]
    return True


def _bm25_ranking(query: str, collection, limit: int, source_filter: str | None,
                  scope: dict | None = None) -> list[str]:
    index, chunks = _get_bm25_index(collection)
    if index is None or not chunks:
        return []

    scores = index.get_scores(_bm25_tokens(query))
    order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)

    ids = []
    for i in order:
        if not _matches_scope(chunks[i], source_filter, scope):
            continue
        ids.append(chunks[i]["id"])
        if len(ids) >= limit:
            break
    return ids


def _dense_ranking(query: str, collection, limit: int, source_filter: str | None,
                   scope: dict | None = None) -> list[str]:
    q_vec = embed_query(query)
    # v10.5: exact-match metadata `where` (valid in Chroma, unlike the old
    # substring attempt). scope = {"product": key} or {"category": key},
    # set directly at upload time, so this filters server-side, fast and
    # exact — no over-fetch/post-filter, no filename matching.
    where = None
    if source_filter:
        where = {"source": source_filter}
    elif scope and "product" in scope:
        # Either the flag (a document tagged to this product, possibly among
        # others) or the old single-value field, so chunks written before the
        # flags still match without a rebuild.
        where = {"$or": [{"prod_" + scope["product"]: True},
                         {"product": scope["product"]}]}
    elif scope and "category" in scope:
        where = {"category": scope["category"]}
    if where:
        res = collection.query(query_embeddings=[q_vec.tolist()],
                               n_results=limit, where=where)
        return res["ids"][0] if res.get("ids") else []
    res = collection.query(query_embeddings=[q_vec.tolist()], n_results=limit)
    return res["ids"][0] if res.get("ids") else []


def apply_arm_guarantee(ranked_ids, keep_n, *arms, guarantee):
    """Keep the RRF top `keep_n`, then append the top `guarantee` of each
    individual arm that the RRF cut dropped. Pure, so the behaviour this
    fixes can be pinned by a test -- see tests/test_rrf.py.

    Order matters and is deliberate: the RRF head comes first and keeps its
    own order, rescues follow in arm order. Nothing that RRF ranked highly
    is displaced; the rescues only ever EXTEND the window. Returns the new
    candidate list, whose length is the caller's new keep_n.

    NOTE for future edits: the caller must not re-trim the result to top_k
    afterwards. Twice now the candidate margin computed here has been
    silently thrown away by a downstream `break` at top_k, which is the
    whole bug this function exists to prevent.
    """
    head = list(ranked_ids[:keep_n])
    if guarantee <= 0:
        return head
    seen = set(head)
    rescued = []
    for arm in arms:
        for cid in arm[:guarantee]:
            if cid not in seen:
                seen.add(cid)
                rescued.append(cid)
    return head + rescued


def retrieve_from_db(
    query: str,
    top_k: int = 10,
    source_filter: str | None = None,
    scope: dict | None = None,
) -> list[dict]:
    """
    Hybrid retrieval over the full corpus, merged via RRF.

    source_filter, if given, scopes BOTH rankings to chunks from that one
    source filename — used by the "ask more about this document" flow
    triggered from a clickable source in the UI.

    Returns chunk dicts with 'id', 'text', 'source', 'retrieval_score'.
    The 'id' is the stable ChromaDB id, used downstream to fetch full
    chunk content on demand (clickable sources) without re-querying.
    """
    collection = get_collection()
    n = collection.count()
    if n == 0:
        return []

    fetch_n = min(max(top_k * 2, top_k), n)

    bm25_ids = _bm25_ranking(query, collection, fetch_n, source_filter, scope)
    dense_ids = _dense_ranking(query, collection, fetch_n, source_filter, scope)

    scores = rrf_merge(bm25_ids, dense_ids, k=RRF_K)
    if not scores:
        return []

    # Keep a margin of candidates (top_k*2) so the downstream reranker has
    # room to reorder before the answering pipeline trims to top_k.
    #
    # 2026-08-28: the margin built here was thrown away again by the
    # `len(results) >= top_k` break below, so the reranker only ever saw
    # top_k. That is not academic. RRF rewards agreement between the two
    # rankings: with RRF_K=60 a chunk found by ONE retriever caps at
    # 1/61 = 0.0164, while anything both rank in their top ten scores
    # ~0.028+. So a chunk ranked #1 by dense and missed entirely by BM25
    # loses to chunks both rank ~7th -- and gets cut before the reranker,
    # which is the one stage that would have recognised it.
    #
    # Measured on "How much does the NV9S validator weigh on its own?":
    # the correct chunk (p25, "Validator NV9S: 1.05 Kg") was dense #1,
    # BM25 absent, RRF rank 20 of 46 -- four places outside a 16 cut. Given
    # to the reranker it scores 0.9928; the table-of-contents chunk that
    # won instead scores 0.2165. This hits spec tables hardest, where dense
    # understands weigh->Weights and BM25 sees only numbers.
    #
    # RETRIEVAL_CANDIDATE_MARGIN is the multiple of top_k handed to the
    # reranker. 1 is the old behaviour, kept so the change can be A/B'd
    # and reverted by config rather than by a deploy.
    ranked_ids = sorted(scores, key=scores.get, reverse=True)[:top_k * 2]
    keep_n = max(top_k, int(round(top_k * RETRIEVAL_CANDIDATE_MARGIN)))

    # Measured 2026-08-28: simply widening keep_n to 2x fixes the single-list
    # case but costs two others and drops mean grounding 0.993 -> 0.954,
    # because 16 extra mediocre candidates displace good ones when the
    # reranker picks its CONTEXT_K. The targeted alternative: keep the RRF
    # order as-is, but guarantee the top ARM_GUARANTEE entries from EACH
    # ranking survive even if RRF buried them. That admits ~2-6 extra
    # candidates rather than 16, which is the whole point -- it rescues the
    # "one retriever is certain, the other has never heard of it" case
    # without diluting the consensus ones. 0 disables.
    #
    # Guarded rather than folded into the helper so that with the knob at 0
    # this is byte-identical to the pre-fix path: ranked_ids stays the full
    # top_k*2 list and the loop below backfills past any id missing from the
    # chunk cache, instead of returning short.
    if RETRIEVAL_ARM_GUARANTEE > 0:
        ranked_ids = apply_arm_guarantee(
            ranked_ids, keep_n, dense_ids, bm25_ids,
            guarantee=RETRIEVAL_ARM_GUARANTEE,
        )
        keep_n = len(ranked_ids)

    _, chunks = _get_bm25_index(collection)
    by_id = {c["id"]: c for c in chunks}

    # v10.16: with doc2query gone, every ranked id maps directly to a real
    # chunk (no question->parent indirection, no dedupe needed). Any id the
    # dense query returns that isn't in by_id — e.g. a stale kind="query"
    # entry filtered out above — is skipped by the `if not entry` guard.
    results = []
    for doc_id in ranked_ids:
        entry = by_id.get(doc_id)
        if not entry:
            continue
        results.append({
            "id": entry["id"],
            "text": entry["text"],
            "source": entry["source"],
            "page": entry.get("page"),
            # v15.2: product/category were collected into the chunk cache above
            # but dropped here, so every result reached the caller with
            # product=None. Scoped retrieval still worked (that filters inside
            # Chroma on the stored metadata), which is why this went unnoticed
            # -- but anything reasoning about the products a result set SPANS
            # saw nothing. Concretely: the unscoped product-disambiguation
            # check in main.py computed an empty span and never fired, so
            # "give pinout for nv9?" with no product silently blended the
            # NV9 Spectral and NV9USB+ manuals into one answer about pin 13
            # instead of asking which product was meant.
            "product": entry.get("product", ""),
            "category": entry.get("category", ""),
            # The heading path ingest wrote from the document's own
            # outline ("Accessing my device ... > Step 1: ..."). Dropped
            # here until 2026-09-22, the same way product/category were
            # in v15.2 -- and for the same reason it went unnoticed, that
            # nothing downstream had needed it yet. complete_procedures
            # reads the parent/child relation off it.
            "section": entry.get("section", ""),
            "retrieval_score": round(scores[doc_id], 6),
        })
        if len(results) >= keep_n:
            break

    return results


# ── Fusing several phrasings of one question ─────────────────────────────
#
# A REWRITE MUST NOT BE ABLE TO LOSE THE ORIGINAL'S HITS.
#
# condense_query resolves a follow-up into a standalone query, and until
# 2026-09-22 that rewrite REPLACED the question for retrieval. Rewriting is
# lossy in both directions: naming the entities explicitly can pull the
# embedding away from the phrasing that actually matched, so a query that
# retrieved the right page can be rewritten into one that does not, with no
# way back. Measured on "what are the power requirements for this setup?":
# as typed, the PSU page reranked #1 (0.9348) with a clean cliff to 0.0843;
# resolved to name both products, it was not retrieved at all and a
# firmware-programming page entered context instead.
#
# So retrieve on BOTH phrasings and fuse. Neither is trusted to be the
# better one -- that judgement is what keeps being wrong -- and fusion means
# the question can only gain candidates a phrasing found, never lose them.
# This is also what makes dropping the regex gate in condense_query safe: an
# over-eager rewrite now costs candidates it has to win on merit, not the
# original's hits.
#
# Deliberately NOT "pick the query whose top score is higher". Comparing
# scores across two different queries is comparing two different cross-
# encoder inputs, and the RRF scores here are rank-derived and far too flat
# to discriminate (see the note at the rerank call in main.py).


def fuse_ranked_results(result_sets, guarantee=RETRIEVAL_ARM_GUARANTEE):
    """Merge per-query result lists, keeping the PRIMARY query's candidates
    in full and adding the top `guarantee` of each other phrasing.

    `result_sets` is a list of retrieve_from_db outputs, best-first, with the
    query the caller would have used on its own FIRST. There is deliberately
    no `top_k`: the primary list is already sized by retrieve_from_db, and
    trimming here is what broke this (see below).

    STRICTLY ADDITIVE, and that is the whole design. Two failed attempts,
    both measured 2026-09-22 against the live index:

      * RRF-fuse the two lists and trim to top_k. A rewrite drops 9-13 of
        the 16 candidates the question had as typed, so fusing two 16-lists
        into 16 slots evicts about half of each. On "what is the power
        required to run both at once" it evicted the chunk that actually
        answers it ("requires a stable 24VDC / 3.5A ... whilst the SCS
        requires 24V DC 7.5A", reranked 0.9975) and the turn was left with
        generic power-supply boilerplate. Fusion that can lose the rewrite's
        hits is the same bug as a rewrite that loses the original's, pointed
        the other way.
      * Reserve an equal share of top_k per query. Same failure: the
        reranker's best pick routinely sits at retrieval rank 9-16, outside
        any half-share reserve, so it was still evicted.

    The lesson is the one from the bm25/dense arms on 2026-08-28: rescue a
    few candidates one ranking is certain about, do not re-partition the
    window. So this adds ~3-6 candidates rather than 16, for the same reason
    and through the same helper.

    ORDER IS DELIBERATELY NOT FUSED. main.py reranks the FULL candidate list
    and only then truncates to CONTEXT_K, so what reaches the prompt depends
    on membership and cross-encoder score, not on the order retrieval
    returned. An RRF pass over the two lists would compute an ordering that
    nothing reads, and its only real effect -- deciding who gets trimmed --
    is the effect being removed here.

    `retrieval_score` is kept as the BEST score the chunk reached under any
    one phrasing, so it stays on the single-query scale and logged values
    remain comparable. Nothing gates on it (every gate reads `rerank_score`).
    """
    result_sets = [rs for rs in result_sets if rs]
    if not result_sets:
        return []
    if len(result_sets) == 1:
        return result_sets[0]

    by_id = {}
    for rs in result_sets:
        for r in rs:
            prev = by_id.get(r["id"])
            if prev is None:
                by_id[r["id"]] = dict(r)
            elif r.get("retrieval_score", 0.0) > prev.get("retrieval_score", 0.0):
                prev["retrieval_score"] = r["retrieval_score"]

    primary = [r["id"] for r in result_sets[0]]
    others = [[r["id"] for r in rs] for rs in result_sets[1:]]
    ranked_ids = apply_arm_guarantee(primary, len(primary), *others,
                                     guarantee=guarantee)
    return [by_id[cid] for cid in ranked_ids if cid in by_id]


def retrieve_fused(queries, top_k=8, source_filter=None, scope=None):
    """Retrieve for each distinct query and fuse the results.

    Falls through to a plain retrieve_from_db when the queries collapse to
    one, so a turn with no rewrite is bit-for-bit the old path and costs no
    extra retrieval.
    """
    seen, distinct = set(), []
    for q in queries:
        norm = (q or "").strip()
        if not norm or norm.lower() in seen:
            continue
        seen.add(norm.lower())
        distinct.append(norm)

    if not distinct:
        return []
    if len(distinct) == 1:
        return retrieve_from_db(distinct[0], top_k=top_k,
                                source_filter=source_filter, scope=scope)

    result_sets = [
        retrieve_from_db(q, top_k=top_k, source_filter=source_filter,
                         scope=scope)
        for q in distinct
    ]
    return fuse_ranked_results(result_sets)


# ── Procedure completion ─────────────────────────────────────────────────
#
# THE STEPS OF A PROCEDURE ARE THE LEAST RETRIEVABLE PART OF IT.
#
# From the widget transcript of 2026-09-21, "how to get RNDIS working with
# linux?". The corpus holds "Accessing my device in Linux Environment",
# eight chunks, whose Steps 1-4 carry the actual commands. Retrieval
# returned two of those eight:
#
#   rank  3  "...instruction documentation for setting up RNDIS between an
#             ICU device and a local Ethernet adapter in a Linux-based
#             environment:"                                    <- the intro
#   rank  4  "Important Notes: the IP address is static at..."  <- the notes
#   (Steps 1, 2, 3 and 4 were not in the top SIXTEEN)
#
# The model was handed a sentence announcing a procedure, plus its
# footnotes, and not one line of the procedure. It said it could not find
# the answer, which on that context was the correct thing to say.
#
# The cause is general and it is not a tuning problem. A step reads
# "sudo touch /etc/udev/rules.d/80-local.rules". It contains neither
# "RNDIS" nor "Linux" nor any other word a person would type. The parts of
# a document that TALK ABOUT a task always out-retrieve the parts that DO
# it, on both rankings at once -- BM25 has no terms to match and the
# embedding of a shell command is nothing like the embedding of a
# question. Widening the candidate window does not help, because the steps
# are not ranked low, they are ranked nowhere.
#
# So the steps are not retrieved. They are FETCHED, because something that
# was retrieved points at them: a chunk whose own sub-sections are steps is
# the introduction to those steps, and the document says so in its heading
# path. Nothing here guesses at relevance -- the parent/child relation is
# read from `section`, which ingest wrote from the document's own outline.
#
# Deliberately NOT "small documents come in whole", which was the first
# idea and is worse: it fires on every short document for every question,
# spending context on material nothing pointed at, and it cannot help a
# long manual whose procedure has the identical problem.

import re as _re

# "Step 4: Verify..." or "... > Step 4: Verify...". A numbered step in the
# heading, not merely a numbered line in the body -- body numbering is how
# every parts list and pinout is written, and those are not procedures.
_STEP_HEADING = _re.compile(r"(?:^|›\s*)\s*step\s*\d+\b", _re.I)

# Total characters of fetched step text allowed per query. Four thousand is
# roughly two chunks' worth against CHUNK_CHAR_CAP, and the procedures in
# this corpus run 300-800 characters a step, so a four-step procedure fits
# with room over. A procedure too long for this is one the visitor should
# be sent to the document for.
PROCEDURE_COMPLETION_CHARS = int(
    os.getenv("PROCEDURE_COMPLETION_CHARS", "4000"))


def _order_key(chunk_id: str) -> int:
    """Document order, from the `<source>_<n>` id ingest assigns."""
    try:
        return int(str(chunk_id).rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return 0


def complete_procedures(chunks: list[dict],
                        budget: int = PROCEDURE_COMPLETION_CHARS) -> list[dict]:
    """Add the steps that a retrieved chunk introduces but does not contain.

    Returns `chunks` with any missing step chunks appended, in document
    order, each marked `fetched_by="procedure_completion"` so the console
    and `_build_sources` can tell a fetched passage from a retrieved one.

    Returns the input unchanged -- and never raises -- when there is
    nothing to add or the collection cannot be read. This runs on every
    query, and a fault here must degrade to today's behaviour rather than
    cost an answer.
    """
    if not chunks:
        return chunks
    try:
        collection = get_collection()
        have = {(c.get("source"), (c.get("section") or "")) for c in chunks}
        by_source: dict[str, dict] = {}
        out = list(chunks)
        spent = 0

        for c in chunks:
            src = c.get("source")
            if not src:
                continue
            if src not in by_source:
                got = collection.get(where={"source": src})
                by_source[src] = {
                    "ids": got.get("ids") or [],
                    "docs": got.get("documents") or [],
                    "metas": got.get("metadatas") or [],
                }
            doc = by_source[src]
            section = (c.get("section") or "").strip()
            if not section:
                continue

            # Does THIS chunk introduce steps? Only a chunk whose own
            # section is the parent of a step heading does, which is what
            # stops every chunk of a procedural document from dragging in
            # the whole document.
            introduces = any(
                (m.get("section") or "").startswith(section + " ›")
                and _STEP_HEADING.search(m.get("section") or "")
                for m in doc["metas"])
            if not introduces:
                continue

            # Every step of this document, not only the nested ones. The
            # outline a PDF yields is not reliably nested: in the Linux
            # document Steps 1 and 2 sit under the introduction and Steps
            # 3 and 4 sit at the top level, purely because of how the
            # headings were styled. Taking only the children would fetch
            # half a procedure, which is worse than fetching none -- the
            # reader follows it and stops in the middle.
            rows = sorted(zip(doc["ids"], doc["docs"], doc["metas"]),
                          key=lambda r: _order_key(r[0]))
            for cid, text, meta in rows:
                msec = (meta.get("section") or "").strip()
                if not _STEP_HEADING.search(msec):
                    continue
                if (src, msec) in have:
                    continue
                if spent + len(text or "") > budget:
                    logger.info(
                        "procedure completion stopped at the budget "
                        "(%d chars) for %s", budget, src)
                    break
                have.add((src, msec))
                spent += len(text or "")
                out.append({
                    "id": cid, "text": text, "source": src,
                    "page": meta.get("page"), "section": msec,
                    # Ranked BELOW everything retrieved, deliberately. A
                    # fetched chunk did not earn a position; it is here
                    # because a retrieved chunk pointed at it, and the
                    # answering prompt treats passage order as a hint.
                    "retrieval_score": 0.0,
                    "fetched_by": "procedure_completion",
                })
        if spent:
            logger.info("procedure completion added %d chunk(s), %d chars",
                        len(out) - len(chunks), spent)
        return out
    except Exception as exc:
        logger.warning(f"procedure completion skipped: {exc}")
        return chunks


def sources_titled_for(words: list[str], product: str) -> list[str]:
    """Documents whose FILENAME contains every word, tagged to `product`.

    A metadata lookup, not a retrieval: no embedding, no ranking, and it
    reuses the BM25 chunk cache, so it costs one pass over a list that is
    already in memory.

    It exists because ranking cannot be fixed into covering one case.
    "Can I use MyCheckr with linux?" should find "Accessing my device in
    Linux Environment", which the operator tagged to MyCheckr at upload.
    The document never uses the word "MyCheckr", so the cross-encoder
    correctly scores it as off-topic against a question naming MyCheckr
    and the protocol pages outrank it. Forcing candidates past the rerank
    cut is the experiment this repo already ran and rejected -- it cost
    two regressions and took mean grounding from 0.993 to 0.954.

    Tagging is read with the SAME predicate retrieval scopes with
    (_matches_scope), because the operator's assignment at upload is the
    authoritative one. catalog.product_for_source is a different mapping
    -- filename substrings, used for ingest defaults -- and it reports
    this document as `biometrics_general` alone, which is not what the
    operator actually chose.
    """
    if not words or not product:
        return []
    try:
        _index, chunks = _get_bm25_index(get_collection())
    except Exception as exc:
        logger.warning(f"sources_titled_for unavailable: {exc}")
        return []
    out = []
    for c in chunks or []:
        src = c.get("source") or ""
        if src in out:
            continue
        stem = src.rsplit(".", 1)[0].lower()
        if not all(w in stem for w in words):
            continue
        if _matches_scope(c, None, {"product": product}):
            out.append(src)
    return out
