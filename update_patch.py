with open('C:/Users/antho/.windsurf/cappo-backend/cappo_backend/capability_mount/models.py', 'r', encoding='utf-8') as f:
    m_content = f.read()

old_enum = """class UnmountReason(str, Enum):
    TASK_COMPLETE = "task_complete"
    TOKEN_EXPIRY = "token_expiry"
    EXPLICIT_TERMINATE = "explicit_terminate\""""

new_enum = """class UnmountReason(str, Enum):
    TASK_COMPLETE = "task_complete"
    TOKEN_EXPIRY = "token_expiry"
    EXPLICIT_TERMINATE = "explicit_terminate"
    USER_REVOKED = "user_revoked\""""

if old_enum in m_content:
    m_content = m_content.replace(old_enum, new_enum, 1)
    with open('C:/Users/antho/.windsurf/cappo-backend/cappo_backend/capability_mount/models.py', 'w', encoding='utf-8') as f:
        f.write(m_content)
    print('Updated models.py')
else:
    print('models.py already updated or not found')

with open('C:/Users/antho/.windsurf/cappo-backend/cappo_backend/capability_mount/effects.py', 'r', encoding='utf-8') as f:
    e_content = f.read()

old_pat = '_RESOURCE_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")'
new_pat = '_RESOURCE_PATTERN = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")'

if old_pat in e_content:
    e_content = e_content.replace(old_pat, new_pat, 1)
    with open('C:/Users/antho/.windsurf/cappo-backend/cappo_backend/capability_mount/effects.py', 'w', encoding='utf-8') as f:
        f.write(e_content)
    print('Updated effects.py')
else:
    print('effects.py already updated')
