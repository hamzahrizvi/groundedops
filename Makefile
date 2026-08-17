# GroundedOps dev shortcuts
#
# NOTE: the "dev"/"rebuild"/"prod"/"logs"/"down" targets are for deploying
# this as a real service (Docker Compose files live in docker/). Local,
# day-to-day development and admin use runs natively — see run.cmd /
# run.ps1 at the repo root, which is faster to iterate on and matches how
# this app is actually packaged for install (see QUICKSTART.md).
.PHONY: dev rebuild prod logs eval reload down

dev:            ## hot-reload Docker stack (uses docker/docker-compose.override.yml)
	cd docker && docker compose up -d
	@echo "http://localhost:8080  (backend hot-reloads on save)"

rebuild:        ## rebuild images and restart (after dependency changes)
	cd docker && docker compose up -d --build

prod:           ## production run WITHOUT the dev override
	cd docker && docker compose -f docker-compose.yml up -d --build

logs:           ## tail backend logs (watch 'doc2query provider = ...')
	cd docker && docker compose logs -f backend

eval:           ## run the eval gate locally (needs corpus ingested)
	cd src && GENERATION_MODE=local python eval.py

reload:         ## re-ingest everything in ./corpus (admin password required)
	curl -s -X POST http://localhost:8080/api/ingest/reload_folder \
	  -H "X-Admin-Password: $${ADMIN_PASSWORD:-admin}" | python -m json.tool

down:           ## stop the Docker stack (keeps volumes)
	cd docker && docker compose down
