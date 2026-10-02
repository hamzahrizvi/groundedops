"""9.6: check the FAQ answers nobody read against their own manual.

/autogenerate saved 157 drafts without a person approving them, and the
widget badged every one "Reviewed answer". This runs the LLM verifier
(main._llm_verified, thinking on) 3 times per unedited generated entry
against the top-3 chunks of that entry's own source, and records:

    verified: true        3/3 supported -> may wear the "Reviewed" badge
    verified: false       0/3 supported -> a person should read it
    verified: "unstable"  anything between

Harvested entries are verbatim manual text, so checking them against the
manual is tautological; edited/manual/curated ones a person already read.

    cd src && ../.venv/Scripts/python verify_faq_entries.py [--limit N] [--dry-run]

Prints the stable rejects: read those against the PDF, then edit or delete.
"""
import argparse
import sys

RUNS = 3


def main_() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    # A cp1252 console dies on the first "Ω" in a question.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # torch must first import on the main thread; main pulls it in.
    import main
    import faq_store
    import retrieval_db
    from llm import judging

    todo = [e for e in faq_store._load()
            if e.get("origin") == "generated" and not e.get("edited")
            and e.get("source")]
    if args.limit:
        todo = todo[:args.limit]
    verdicts = {}
    try:
        for i, e in enumerate(todo, 1):
            chunks = retrieval_db.retrieve_from_db(e["question"], top_k=3,
                                                   source_filter=e["source"])
            with judging():
                passes = sum(main._llm_verified(e["answer"], chunks,
                                                question=e["question"])
                             for _ in range(RUNS))
            verdicts[e["id"]] = (True if passes == RUNS else
                                 False if passes == 0 else "unstable")
            print(f"[{i}/{len(todo)}] {passes}/{RUNS} {e['question'][:70]!r}",
                  flush=True)
    finally:
        # Even on a crash: the judging calls already made are worth keeping.
        if not args.dry_run and verdicts:
            # Re-read so an edit made while this ran is not overwritten.
            with faq_store._lock:
                items = faq_store._load()
                for it in items:
                    if it.get("id") in verdicts and not it.get("edited"):
                        it["verified"] = verdicts[it["id"]]
                faq_store._save(items)
            faq_store._invalidate_cache()

    vals = list(verdicts.values())
    print(f"\nverified {vals.count(True)}, unstable {vals.count('unstable')}, "
          f"stable rejects {vals.count(False)} of {len(vals)}"
          f"{' (dry run, nothing written)' if args.dry_run else ''}")
    by_id = {e["id"]: e for e in todo}
    for fid, v in verdicts.items():
        if v is False:
            e = by_id[fid]
            print(f"  REJECT {fid} [{e['source']}] {e['question']}\n"
                  f"         -> {e['answer'][:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main_())
