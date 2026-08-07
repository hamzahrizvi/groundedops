# GroundedOps dev shortcuts
.PHONY: dev rebuild prod logs eval reload down

dev:            ## hot-reload dev stack (uses docker-compose.override.yml)
	docker compose up -d
	@echo "http://localhost:8080  (backend hot-reloads on save)"

rebuild:        ## rebuild images and restart (after dependency changes)
	docker compose up -d --build

prod:           ## production run WITHOUT the dev override
	docker compose -f docker-compose.yml up -d --build

logs:           ## tail backend logs (watch 'doc2query provider = ...')
	docker compose logs -f backend

eval:           ## run the eval gate locally (needs corpus ingested)
	cd src && GENERATION_MODE=local python eval.py

reload:         ## re-ingest everything in ./corpus (admin password required)
	curl -s -X POST http://localhost:8080/api/ingest/reload_folder \
	  -H "X-Admin-Password: $${ADMIN_PASSWORD:-admin}" | python -m json.tool

down:           ## stop everything (keeps volumes)
	docker compose down
