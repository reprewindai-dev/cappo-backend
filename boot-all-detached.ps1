$ErrorActionPreference = 'Stop'

function Start-Detached {
    param([string]$Name, [string]$Dir, [string]$Cmd, [string]$ArgsList)
    Write-Host "[*] Starting $Name..." -ForegroundColor Cyan
    Start-Process -FilePath $Cmd -ArgumentList $ArgsList -WorkingDirectory $Dir -WindowStyle Hidden
}

# Stop existing processes first to be safe
Get-Process -Name "node", "python", "uvicorn" -ErrorAction SilentlyContinue | Where-Object { $_.Path -match "\.veklom" -or $_.Path -match "cappo-backend" -or $_.Path -match "gnomledger" -or $_.Path -match "veklom-byos" -or $_.Path -match "cAPI" -or $_.Path -match "veklom-control-plane" } | Stop-Process -Force -ErrorAction SilentlyContinue

Start-Sleep 2

# 1. CAPPO
Start-Detached "CAPPO" "C:\Users\antho\.windsurf\cappo-backend" "uv" "run uvicorn cappo_backend.main:app --port 8002 --host 127.0.0.1"

# 2. PGL
Start-Detached "PGL" "C:\Users\antho\.windsurf\gnomledger" "uv" "run uvicorn backend.app.main:app --port 8001 --host 127.0.0.1"

# 3. BYOS Backend
Start-Detached "BYOS" "C:\Users\antho\.windsurf\veklom-byos-backend-2" "uv" "run uvicorn backend.apps.api.main:app --port 8088 --host 127.0.0.1"

# 4. cAPI
Start-Detached "cAPI" "C:\Users\antho\.windsurf\cAPI" "cmd.exe" "/c pnpm dev -p 3003"

# 5. Control Plane
Start-Detached "ControlPlane" "C:\Users\antho\.windsurf\veklom-control-plane" "cmd.exe" "/c pnpm dev -p 3002"

Write-Host "Services started detached. Waiting for 15s to check health..."
