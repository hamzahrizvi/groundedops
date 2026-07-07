# Publishing v9.1.5 — run these locally (no credentials needed by anyone else)

# 0. Copy the release files into your repo root first:
#    README.md, requirements.txt (unchanged, included for completeness),
#    install.ps1, install.sh, start.ps1, start.sh
#    (plus any v9.1.x change files not yet applied)

# 1. Sanity: make sure secrets/artifacts are ignored
#    .gitignore should contain: chroma_db/  logs.jsonl  eval_results.json
#    __pycache__/  frontend/node_modules/  frontend/dist/  .env
#    And confirm no API keys are hardcoded anywhere:
git grep -iE "sk-[a-zA-Z0-9]" -- "*.py" "*.js" "*.jsx" || echo "clean"

# 2. Decide about the PDFs (ITL manuals + internal ICU doc).
#    If the repo is public: gitignore them and note "bring your own
#    corpus" in the README. If private/internal: fine to include.

# 3. Commit, tag, push
git add -A
git commit -m "v9.1.5: online/free modes, multi-provider keys, persistent chats, installers, README"
git tag v9.1.5
git push origin main --tags

# 4. Create the GitHub release (paste RELEASE_NOTES_v9.1.5.md)
#    Web UI: Releases -> Draft a new release -> tag v9.1.5
#    or CLI: gh release create v9.1.5 --title "GroundedOps v9.1.5" --notes-file RELEASE_NOTES_v9.1.5.md
