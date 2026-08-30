$f = "C:\Users\antho\.windsurf\cappo-backend\scripts\n8n_18_integration_probe.py"
$c = Get-Content $f
$c = $c -replace '\(REPO / "\.env\.test"\)', '(Path("C:/Users/antho/.veklom/.env"))'
Set-Content $f -Value $c
