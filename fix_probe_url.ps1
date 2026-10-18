$f = "C:\Users\antho\.windsurf\cappo-backend\scripts\n8n_18_integration_probe.py"
$c = Get-Content $f
$c = $c -replace 'http://127.0.0.1:5678', 'https://n8n.veklom.com'
Set-Content $f -Value $c
