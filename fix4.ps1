$f = "C:\Users\antho\.veklom\runtime\veklom-local.ps1"
$c = Get-Content $f
$c = $c -replace '\[int\]\$Retries=20', '[int]$Retries=60'
Set-Content $f -Value $c
