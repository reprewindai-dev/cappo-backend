$ErrorActionPreference = 'Stop'
function Run-Svc {
    param($Name, $Dir, $Cmd)
    Write-Host "Starting $Name in $Dir..."
    $psCmd = "cd '$Dir'; $Cmd"
    Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $psCmd -WindowStyle Normal
}

Run-Svc "CAPPO" "C:\Users\antho\.windsurf\cappo-backend" "$env:DATABASE_URL='postgresql+psycopg2://test_user:rotated_sec_99_cappo@127.0.0.1:5432/cappo_test'; $env:OLLAMA_KEEP_ALIVE='300'; uv run uvicorn cappo_backend.main:app --port 8002 --host 127.0.0.1"

Run-Svc "PGL" "C:\Users\antho\.windsurf\gnomledger" "$env:PGL_DB_URL='sqlite:///C:/Users/antho/.windsurf/gnomledger/data/pgl.sqlite3'; uv run uvicorn backend.app.main:app --port 8001 --host 127.0.0.1"

Run-Svc "BYOS" "C:\Users\antho\.windsurf\veklom-byos-backend-2" "$env:DATABASE_URL='postgresql+psycopg2://test_user:rotated_sec_99_cappo@127.0.0.1:5432/cappo_test'; $env:OLLAMA_KEEP_ALIVE='300'; uv run uvicorn backend.apps.api.main:app --port 8088 --host 127.0.0.1"

Run-Svc "cAPI" "C:\Users\antho\.windsurf\cAPI" "pnpm dev -p 3003"

Run-Svc "ControlPlane" "C:\Users\antho\.windsurf\veklom-control-plane" "pnpm dev -p 3002"

Run-Svc "Cloudflared" "C:\Users\antho\.windsurf\cappo-backend" "cloudflared tunnel run 0061f2f2-3eaf-4fb6-add3-5916f8cc651c"

Write-Host "Services launched."
