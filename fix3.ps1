$f = "C:\Users\antho\.veklom\runtime\veklom-local.ps1"
$c = Get-Content $f
$c = $c -replace 'npx n8n start', 'npx -y n8n start'
Set-Content $f -Value $c
