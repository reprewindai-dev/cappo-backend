$f = "C:\Users\antho\.windsurf\cAPI\package.json"
$c = Get-Content $f
$c = $c -replace '"dev": "next dev"', '"dev": "next dev --webpack"'
Set-Content $f -Value $c
