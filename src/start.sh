#!/usr/bin/env bash
uvicorn main:app --port 8000 &
BACK=$!
(cd frontend && npm run dev) &
FRONT=$!
echo "Backend http://127.0.0.1:8000 | Frontend http://localhost:5173"
trap "kill $BACK $FRONT" INT
wait
