$f = "C:\Users\antho\.veklom\runtime\veklom-local.ps1"
$c = Get-Content $f
$c = $c -replace 'node node_modules/n8n/bin/n8n start', 'npx n8n start'
Set-Content $f -Value $c
