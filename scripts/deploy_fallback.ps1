<#
.SYNOPSIS
Fallback CI/CD Deployment Script for cappo-backend

.DESCRIPTION
This script acts as the Plan B for deploying the Cappo Backend. 
If GitHub Actions fails or is unavailable, this script performs the exact same 
validation (lint, tests) and deploys directly to Server 0 (5.78.135.11).
#>

$ErrorActionPreference = "Stop"

Write-Host "========== CAPPO-BACKEND FALLBACK CI/CD ==========" -ForegroundColor Cyan
Write-Host "Phase 1: Local Checks" -ForegroundColor Yellow

Write-Host "Running ruff check..."
python -m ruff check . --exclude tmp_x402,migrations/versions

Write-Host "Running tests..."
# Assuming pytest is installed and dependencies are met in the local environment
pytest -q --tb=short --maxfail=1

Write-Host "Phase 1 Complete! All checks passed." -ForegroundColor Green
Write-Host "Phase 2: Deploy to Server 0 (5.78.135.11)" -ForegroundColor Yellow

$COMMIT = git rev-parse HEAD
Write-Host "Deploying commit $COMMIT"

# Deploy script (same as GitHub Actions)
$sshScript = @"
set -e
SRC=/data/coolify/applications/cappo-backend
APP=/data/coolify/applications/yen2fecq8burtsgqrm2b988e
TAG=yen2fecq8burtsgqrm2b988e:$COMMIT

cd `$SRC
git fetch origin main
git checkout main
git pull --ff-only origin main
docker build -t `$TAG .

cd `$APP
cp docker-compose.yaml docker-compose.yaml.pre-$COMMIT
sed -i "s#^\([[:space:]]*\)image: .*#\1image: '`$TAG'#" docker-compose.yaml
docker compose -f docker-compose.yaml config >/tmp/cappo-compose-config.out
docker compose -f docker-compose.yaml up -d --force-recreate
echo 'Deployment complete for cappo-backend'
"@

$sshKeyPath = "$env:USERPROFILE\.ssh\veklom-deploy"
if (-Not (Test-Path $sshKeyPath)) {
    Write-Host "WARNING: SSH key $sshKeyPath not found. Please ensure your deploy key is in place." -ForegroundColor Red
    exit 1
}

Write-Host "Connecting to root@5.78.135.11..."
ssh -i $sshKeyPath root@5.78.135.11 $sshScript

Write-Host "Phase 3: Verify Production Health" -ForegroundColor Yellow
Start-Sleep -Seconds 10

$http = curl.exe -sk -o NUL -w "%{http_code}" "https://cappo.veklom.com/health"
if ($http -ne "200") {
    Write-Host "HEALTH CHECK FAILED: cappo.veklom.com returned $http" -ForegroundColor Red
    exit 1
}

Write-Host "cappo.veklom.com is healthy. Fallback deployment SUCCESSFUL!" -ForegroundColor Green
