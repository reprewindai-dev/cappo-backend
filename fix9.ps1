$f = "C:\Users\antho\.veklom\runtime\veklom-local.ps1"
$c = Get-Content $f
$c = $c -replace 'pnpm dev -p 3003', 'npm run dev -- -p 3003'
$c = $c -replace 'pnpm dev -p 3002', 'npm run dev -- -p 3002'
Set-Content $f -Value $c
