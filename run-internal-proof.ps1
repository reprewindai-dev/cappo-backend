$ErrorActionPreference = 'Stop'
Write-Host "Bringing up internal stack..." -ForegroundColor Cyan
C:\Users\antho\.veklom\runtime\veklom-local.ps1 up

Write-Host "Running Internal Proof Pack..." -ForegroundColor Cyan
C:\Users\antho\.windsurf\cappo-backend\scripts\run-undeniable-proof.ps1

Write-Host "Bringing down internal stack..." -ForegroundColor Cyan
C:\Users\antho\.veklom\runtime\veklom-local.ps1 down
