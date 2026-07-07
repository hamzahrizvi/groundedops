# GroundedOps launcher — starts backend + frontend in separate windows
Start-Process powershell -ArgumentList "-NoExit","-Command","uvicorn main:app --port 8000"
Start-Sleep -Seconds 2
Start-Process powershell -ArgumentList "-NoExit","-Command","cd frontend; npm run dev"
Write-Host "Backend:  http://127.0.0.1:8000"
Write-Host "Frontend: http://localhost:5173  (opens in Online mode; key popup on first run)"
