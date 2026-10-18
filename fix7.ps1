$f = "C:\Users\antho\.windsurf\cappo-backend\scripts\n8n_18_integration_probe.py"
$c = Get-Content $f
$c = $c -replace "if record is None:\n            print\('FAILED: Physical consequence did not occur.'\)\n            return False", "if record is None:\n            print('FAILED: Physical consequence did not occur.')\n            return False\n        \n        print('Testing duplicate delivery rejection...')\n        dup_response = httpx.post('http://127.0.0.1:5678/webhook/governed-webhook', json={'veklom_authority': token, 'data': {'action': connector.append_action, 'content': content}}, timeout=20)\n        if dup_response.status_code == 200:\n             print('FAILED: Duplicate delivery succeeded!')\n             return False\n"
Set-Content $f -Value $c
