$f = "C:\Users\antho\.veklom\runtime\veklom-local.ps1"
$c = Get-Content $f
$c = $c -replace 'set N8N_PORT=5678&& call node_modules\\\\\.bin\\\\n8n\.cmd start', 'set N8N_PORT=5678
call node_modules\.bin\n8n.cmd start'
Set-Content $f -Value $c
