$ErrorActionPreference = 'Stop'

function Start-Process-Hidden {
    param([string]$Name, [scriptblock]$ScriptBlock)
    Write-Host "[*] Starting $Name..." -ForegroundColor Cyan
    $job = Start-Job -Name $Name -ScriptBlock $ScriptBlock
    return $job
}

Write-Host "Booting all Veklom services..." -ForegroundColor Green

# 1. CAPPO
Start-Process-Hidden "CAPPO" {
    $env:DATABASE_URL = "postgresql+psycopg2://test_user:rotated_sec_99_cappo@127.0.0.1:5432/cappo_test"
    $env:OLLAMA_KEEP_ALIVE = "300"
    cd C:\Users\antho\.windsurf\cappo-backend
    uv run uvicorn cappo_backend.main:app --port 8002 --host 127.0.0.1 > cappo.log 2>&1
}

# 2. PGL
Start-Process-Hidden "PGL" {
    $env:PGL_DB_URL = "sqlite:///C:/Users/antho/.windsurf/gnomledger/data/pgl.sqlite3"
    cd C:\Users\antho\.windsurf\gnomledger
    uv run uvicorn backend.app.main:app --port 8001 --host 127.0.0.1 > pgl.log 2>&1
}

# 3. BYOS Backend
Start-Process-Hidden "BYOS" {
    $env:DATABASE_URL = "postgresql+psycopg2://test_user:rotated_sec_99_cappo@127.0.0.1:5432/cappo_test"
    $env:OLLAMA_KEEP_ALIVE = "300"
    cd C:\Users\antho\.windsurf\veklom-byos-backend-2
    uv run uvicorn backend.apps.api.main:app --port 8088 --host 127.0.0.1 > byos.log 2>&1
}

# 4. cAPI
Start-Process-Hidden "cAPI" {
    cd C:\Users\antho\.windsurf\cAPI
    pnpm dev -p 3003 > capi.log 2>&1
}

# 5. Control Plane
Start-Process-Hidden "ControlPlane" {
    cd C:\Users\antho\.windsurf\veklom-control-plane
    pnpm dev > controlplane.log 2>&1
}

Write-Host "Waiting 15 seconds for services to boot..."
Start-Sleep -Seconds 15

$ports = @(3002, 8088, 3003, 8002, 8001)
foreach ($port in $ports) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        Write-Host "Port $port : Alive ($($response.StatusCode))" -ForegroundColor Green
    } catch {
        Write-Host "Port $port : $(.Exception.Message)" -ForegroundColor Yellow
    }
}
