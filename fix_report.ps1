$f = "C:\Users\antho\.gemini\antigravity\brain\f2dbdfe7-677a-40de-a1bc-9512196b3ad0\N8N_18_19_FINAL_TRUTHFUL_PROOF.md"
$c = Get-Content $f

$c = $c -replace 'N8N-18/19 Truthful Local Proof Gate', 'N8N-18 Truthful Local Proof Gate'
$c = $c -replace 'cryptographically erase all prior accounts and API keys', 'Removed all prior PGL accounts and API-key records and verified zero active keys remained.'
$c = $c -replace 'cryptographically ignores duplicates\.', 'durably rejects duplicate execution IDs and prevents duplicate physical consequence.'

# Fix the Test 1 section
$c = $c -replace 'Target connector was never engaged.', "Target connector was never engaged.
* **Protocol/UX Result:** NEEDS REPAIR (Current status: HTTP 500; Desired status: explicit governed rejection 4xx)"

$c = $c -replace 'The system is now prepared for N8N-19 \(Cloudflare public ingress routing\)\.', "The system is now prepared for N8N-19 (Cloudflare public ingress routing).

> **N8N-18: VERIFIED_LOCAL — governed live-fire execution and duplicate-consequence suppression demonstrated against a physical target.**
> **N8N-19: READY_FOR_PROOF — extend the identical authority and consequence invariants across Cloudflare public ingress without granting Cloudflare or n8n authority over finality.**"

Set-Content "C:\Users\antho\.gemini\antigravity\brain\f2dbdfe7-677a-40de-a1bc-9512196b3ad0\N8N_18_VERIFIED_LOCAL_PROOF.md" -Value $c
Remove-Item $f
