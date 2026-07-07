# Two-track publish: benchmark snapshot -> generic main (v9.2.0)

# ── STEP 0: decide repo visibility FIRST ─────────────────────────────
# A tag/branch on a PUBLIC repo is fetchable like main. If the repo will
# be public, the benchmark snapshot must ALSO exclude: keys (all forms),
# the internal ICU API doc, logs.jsonl. The vendor user manuals are your
# call (they may be publicly distributed docs — verify).
# Simplest safe layout: repo public + generic; benchmark snapshot lives
# in a PRIVATE fork/branch you can show on request; BENCHMARKS.md tells
# the performance story publicly.

# ── STEP 1: snapshot the reproducible benchmark state (BEFORE deleting) ──
git add -A
git commit -m "benchmark snapshot: corpus-specific eval suite + baselines (16/16)"
git tag v9.1.5-benchmark
# private-branch alternative (recommended if repo goes public):
#   git branch benchmark

# ── STEP 2: strip corpus-specific / private files ────────────────────
git rm -r --cached src/uploads src/chroma_db 2>$null
Remove-Item -Recurse -Force src\uploads, src\chroma_db
Remove-Item src\logs.jsonl, src\eval_results.json -ErrorAction SilentlyContinue
Remove-Item src\eval_baseline.json, src\eval_baseline_16.json, src\eval_cases_16.json -ErrorAction SilentlyContinue
Remove-Item src\eval_cases_extensive.json, src\eval_cases_api.json -ErrorAction SilentlyContinue
Remove-Item src\deepseek_key, src\.deepseek_key.enc -ErrorAction SilentlyContinue
Remove-Item src\deepseek_env_20260611_36d9c2.txt, src\cls -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force src\legacy -ErrorAction SilentlyContinue
Remove-Item GIT_STEPS.md -ErrorAction SilentlyContinue

# ── STEP 3: drop in the generic files from this package ──────────────
#   BENCHMARKS.md            -> repo root
#   src/eval_cases.json      -> replaces the ITL suite (template)
# README: add a "Bring your own corpus" note + link BENCHMARKS.md
# .gitignore: ensure uploads/ chroma_db/ logs.jsonl *.enc deepseek_key
#             eval_results.json eval_baseline*.json are listed

# ── STEP 4: commit generic main and tag the release ──────────────────
git add -A
git commit -m "v9.2.0: generic release — corpus removed, eval template + BENCHMARKS.md added"
git tag v9.2.0
git push origin main --tags

# ── STEP 5 (only if going public): history check ─────────────────────
# Deleting now does NOT remove past commits. If uploads/chroma_db/keys
# were ever committed and the repo goes public, either scrub with
# git filter-repo, or publish the cleaned tree as a FRESH public repo
# (single initial commit) and keep this one private. If any key file is
# in history: rotate the key regardless.
git log --all --oneline -- src/uploads src/chroma_db src/deepseek_key src/logs.jsonl
