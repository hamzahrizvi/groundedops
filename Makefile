# GroundedOps dev shortcuts
#
# The app runs natively: see run.cmd / run.ps1 at the repo root and
# QUICKSTART.md for the install path. These targets assume the backend is
# running on :8000.
.PHONY: eval reload

eval:           ## run the eval gate locally (needs corpus ingested)
	cd src && GENERATION_MODE=local python eval.py

reload:         ## re-ingest everything in ./corpus (admin password required)
	curl -s -X POST http://localhost:8000/ingest/reload_folder \
	  -H "X-Admin-Password: $${ADMIN_PASSWORD:-admin}" | python -m json.tool
