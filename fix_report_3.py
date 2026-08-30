import re
with open(r'C:\Users\antho\.gemini\antigravity\brain\f2dbdfe7-677a-40de-a1bc-9512196b3ad0\N8N_18_VERIFIED_LOCAL_PROOF.md', 'r', encoding='utf-8') as f:
    text = f.read()

text = re.sub(r'Executed wipe\.py to Removed all prior PGL accounts and API-key records and verified zero active keys remained\. from pgl\.sqlite3\.', 'Executed wipe.py to remove all prior PGL accounts and API-key records from pgl.sqlite3, then verified zero active keys remained.', text)
text = text.replace('â€”', '-')
text = text.replace('—', '-')
text = text.replace('', '-')

conclusion = '''## Conclusion

The N8N-18 local truthful proof gate is complete.

The governed local stack cold-started successfully, established a clean PGL authentication baseline, enforced exact capability and audience constraints, produced a real physical target consequence for an authorized request, persisted an execution-bound durable operation record, and prevented duplicate physical consequence when the identical execution was redelivered through n8n.

The wrong-audience test failed closed with no target consequence. Its current HTTP 500 response remains a protocol/UX repair item and should be converted to an explicit governed 4xx rejection, but this does not invalidate the demonstrated security property.

> **N8N-18: VERIFIED_LOCAL - governed live-fire execution and duplicate-consequence suppression demonstrated against a physical target.**

> **N8N-19: READY_FOR_PROOF - extend the same authority, confinement, evidence, and consequence invariants across Cloudflare public ingress without granting Cloudflare or n8n authority over finality.**'''

text = re.sub(r'## Conclusion.*', conclusion, text, flags=re.DOTALL)

with open(r'C:\Users\antho\.gemini\antigravity\brain\f2dbdfe7-677a-40de-a1bc-9512196b3ad0\N8N_18_VERIFIED_LOCAL_PROOF.md', 'w', encoding='utf-8') as f:
    f.write(text)
