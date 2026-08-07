# Corpus folder

Drop source documents (.pdf / .txt / .docx) here. In Docker this folder
is mounted into the backend. To (re)ingest everything in here, use the
admin panel or:

    make reload        # or
    curl -X POST http://localhost:8080/api/ingest/reload_folder -H "X-Admin-Password: admin"

Files already in the vector DB are skipped. After an ingest-LOGIC change
(the UI shows a re-ingest banner), wipe the DB (Reset) then reload here.

NOTE: if this repo is public, gitignore the actual documents — keep only
this README.
