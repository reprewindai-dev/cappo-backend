import re
with open('cappo_backend/capability_mount/service.py', 'r') as f:
    content = f.read()

new_content = re.sub(
    r'(\"action_decision\",)',
    r'\1\n                principal=owner_principal,\n                mount_id=mount_id,\n                timestamp=utc_now().isoformat(),',
    content
)

with open('cappo_backend/capability_mount/service.py', 'w') as f:
    f.write(new_content)
