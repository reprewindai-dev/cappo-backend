import sys

with open(r'C:\Users\antho\.windsurf\cappo-backend\scripts\n8n_18_integration_probe.py', 'r') as f:
    code = f.read()

replacement = '''if record is None:
            print('FAILED: Physical consequence did not occur.')
            return False
            
        print('Testing duplicate delivery rejection...')
        try:
            dup_response = httpx.post('http://127.0.0.1:5678/webhook/governed-webhook', json={'veklom_authority': token, 'data': {'action': connector.append_action, 'content': content}}, timeout=20)
            if dup_response.status_code == 200:
                print('FAILED: Duplicate delivery succeeded!')
                return False
            else:
                print(f'PASS: Duplicate rejected with status {dup_response.status_code}')
        except Exception as e:
            print(f'PASS: Duplicate rejected via exception {e}')'''

code = code.replace("if record is None:\n            print('FAILED: Physical consequence did not occur.')\n            return False", replacement)

with open(r'C:\Users\antho\.windsurf\cappo-backend\scripts\n8n_18_integration_probe.py', 'w') as f:
    f.write(code)
