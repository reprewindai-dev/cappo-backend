$f = "scripts\n8n_19_public_ingress_probe.py"
$c = Get-Content $f
$c = $c -replace "httpx.post\('http://127.0.0.1:8099/governed-action', json=\{'action': connector.append_action, 'content': content\}, headers=\{'Authorization': 'Bearer FAKE'\}\)", "httpx.post('http://127.0.0.1:8099/governed-action', json={'action': connector.append_action, 'content': content}, headers={'Authorization': 'Bearer FAKE'}).raise_for_status()"
Set-Content $f -Value $c
