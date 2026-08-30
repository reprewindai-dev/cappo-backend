$f = "C:\Users\antho\.veklom\runtime\veklom-local.ps1"
$c = Get-Content $f
$c = $c -replace 'npx -y n8n start', 'call node_modules\.bin\n8n.cmd start'
Set-Content $f -Value $c
