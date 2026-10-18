$f = "C:\Users\antho\.cloudflared\config.yml"
$c = Get-Content $f
$c = $c -replace '- service: http_status:404', "- hostname: "n8n.veklom.com"
    service: http://127.0.0.1:5678
  - service: http_status:404"
Set-Content $f -Value $c
