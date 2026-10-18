$ErrorActionPreference = 'Stop'

function Start-Win {
    param([string]$Name, [string]$Dir, [string]$Cmd)
    Write-Host "[*] Starting $Name..." -ForegroundColor Cyan
    Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", "cd $Dir; $Cmd"
}

Get-Process -Name "node", "python", "uvicorn", "powershell" -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -match "veklom" -or $_.MainWindowTitle -match "CAPPO" } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 1

Start-Win "CAPPO" "C:\Users\antho\.windsurf\cappo-backend" "$env:DATABASE_URL='postgresql+psycopg2://test_user:rotated_sec_99_cappo@127.0.0.1:5432/cappo_test'; $env:OLLAMA_KEEP_ALIVE='300'; $host.UI.RawUI.WindowTitle='CAPPO'; uv run uvicorn cappo_backend.main:app --port 8002 --host 127.0.0.1"

Start-Win "PGL" "C:\Users\antho\.windsurf\gnomledger" "$env:PGL_DB_URL='sqlite:///C:/Users/antho/.windsurf/gnomledger/data/pgl.sqlite3'; $host.UI.RawUI.WindowTitle='PGL'; uv run uvicorn backend.app.main:app --port 8001 --host 127.0.0.1"

Start-Win "BYOS" "C:\Users\antho\.windsurf\veklom-byos-backend-2" "$env:DATABASE_URL='postgresql+psycopg2://test_user:rotated_sec_99_cappo@127.0.0.1:5432/cappo_test'; $env:OLLAMA_KEEP_ALIVE='300'; $host.UI.RawUI.WindowTitle='BYOS'; uv run uvicorn backend.apps.api.main:app --port 8088 --host 127.0.0.1"

Start-Win "cAPI" "C:\Users\antho\.windsurf\cAPI" "$host.UI.RawUI.WindowTitle='cAPI'; pnpm dev -p 3003"

Start-Win "ControlPlane" "C:\Users\antho\.windsurf\veklom-control-plane" "$host.UI.RawUI.WindowTitle='ControlPlane'; pnpm dev -p 3002"

Write-Host "Services starting in new windows..."
