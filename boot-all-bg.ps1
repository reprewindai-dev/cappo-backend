$ErrorActionPreference = 'Stop'

function Start-BG {
    param([string]$Name, [string]$Dir, [string]$Cmd)
    Write-Host "[*] Starting $Name..." -ForegroundColor Cyan
    Start-Process powershell.exe -ArgumentList "-WindowStyle Hidden", "-Command", "cd $Dir; $Cmd"
}

# Stop existing processes first to be safe
Get-Process -Name "node", "python", "uvicorn", "powershell" -ErrorAction SilentlyContinue | Where-Object { $_.Path -match "\.veklom" -or $_.Path -match "cappo-backend" -or $_.Path -match "gnomledger" -or $_.Path -match "veklom-byos" -or $_.Path -match "cAPI" -or $_.Path -match "veklom-control-plane" } | Stop-Process -Force -ErrorAction SilentlyContinue

Start-BG "CAPPO" "C:\Users\antho\.windsurf\cappo-backend" "$env:DATABASE_URL='postgresql+psycopg2://test_user:rotated_sec_99_cappo@127.0.0.1:5432/cappo_test'; $env:OLLAMA_KEEP_ALIVE='300'; uv run uvicorn cappo_backend.main:app --port 8002 --host 127.0.0.1 > cappo.log 2>&1"

Start-BG "PGL" "C:\Users\antho\.windsurf\gnomledger" "$env:PGL_DB_URL='sqlite:///C:/Users/antho/.windsurf/gnomledger/data/pgl.sqlite3'; uv run uvicorn backend.app.main:app --port 8001 --host 127.0.0.1 > pgl.log 2>&1"

Start-BG "BYOS" "C:\Users\antho\.windsurf\veklom-byos-backend-2" "$env:DATABASE_URL='postgresql+psycopg2://test_user:rotated_sec_99_cappo@127.0.0.1:5432/cappo_test'; $env:OLLAMA_KEEP_ALIVE='300'; uv run uvicorn backend.apps.api.main:app --port 8088 --host 127.0.0.1 > byos.log 2>&1"

Start-BG "cAPI" "C:\Users\antho\.windsurf\cAPI" "pnpm dev -p 3003 > capi.log 2>&1"

Start-BG "ControlPlane" "C:\Users\antho\.windsurf\veklom-control-plane" "pnpm dev -p 3002 > controlplane.log 2>&1"

Write-Host "Services started."
